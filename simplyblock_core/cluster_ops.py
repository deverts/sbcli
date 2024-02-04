# coding=utf-8
import json
import logging
import math
import os
import time
import uuid

import docker
import requests

from simplyblock_core import utils, scripts, constants, mgmt_node_ops, storage_node_ops
from simplyblock_core.controllers import cluster_events
from simplyblock_core.kv_store import DBController
from simplyblock_core.models.cluster import Cluster

logger = logging.getLogger()


def create_cluster(blk_size, page_size_in_blocks, ha_type, tls,
                   auth_hosts_only, cli_pass, model_ids,
                   cap_warn, cap_crit, prov_cap_warn, prov_cap_crit, ifname):
    logger.info("Installing dependencies...")
    ret = scripts.install_deps()
    logger.info("Installing dependencies > Done")

    if not ifname:
        ifname = "eth0"

    DEV_IP = utils.get_iface_ip(ifname)
    if not DEV_IP:
        logger.error(f"Error getting interface ip: {ifname}")
        return False

    logger.info(f"Node IP: {DEV_IP}")
    ret = scripts.configure_docker(DEV_IP)

    db_connection = f"{utils.generate_string(8)}:{utils.generate_string(32)}@{DEV_IP}:4500"
    ret = scripts.set_db_config(db_connection)

    logger.info("Configuring docker swarm...")
    c = docker.DockerClient(base_url=f"tcp://{DEV_IP}:2375", version="auto")
    try:
        if c.swarm.attrs and "ID" in c.swarm.attrs:
            logger.info("Docker swarm found, leaving swarm now")
            c.swarm.leave(force=True)
            time.sleep(3)

        c.swarm.init()
        logger.info("Configuring docker swarm > Done")
    except Exception as e:
        print(e)

    if not cli_pass:
        cli_pass = utils.generate_string(10)

    logger.info("Deploying swarm stack ...")
    ret = scripts.deploy_stack(cli_pass, DEV_IP, constants.SIMPLY_BLOCK_DOCKER_IMAGE)
    # print(f"Return code: {ret}")
    logger.info("Deploying swarm stack > Done")

    logger.info("Configuring DB...")
    time.sleep(5)
    out = scripts.set_db_config_single()
    logger.info("Configuring DB > Done")

    db_controller = DBController()

    # validate cluster duplicate
    logger.info("Adding new cluster object")
    c = Cluster()
    c.uuid = str(uuid.uuid4())
    c.blk_size = blk_size
    c.page_size_in_blocks = page_size_in_blocks
    c.model_ids = model_ids
    c.ha_type = ha_type
    c.tls = tls
    c.auth_hosts_only = auth_hosts_only
    c.nqn = f"{constants.CLUSTER_NQN}:{c.uuid}"
    c.cli_pass = cli_pass
    c.secret = utils.generate_string(20)
    c.db_connection = db_connection
    if cap_warn and cap_warn > 0:
        c.cap_warn = cap_warn
    if cap_crit and cap_crit > 0:
        c.cap_crit = cap_crit
    if prov_cap_warn and prov_cap_warn > 0:
        c.prov_cap_warn = prov_cap_warn
    if prov_cap_crit and prov_cap_crit > 0:
        c.prov_cap_crit = prov_cap_crit

    c.status = Cluster.STATUS_ACTIVE
    if ha_type == 'ha':
        c.status = Cluster.STATUS_SUSPENDED
    c.updated_at = int(time.time())
    c.write_to_db(db_controller.kv_store)

    cluster_events.cluster_create(c)

    mgmt_node_ops.add_mgmt_node(DEV_IP, c.uuid)

    logger.info("New Cluster has been created")
    logger.info(c.uuid)
    return c.uuid


# Deprecated
def deploy_spdk(node_docker, spdk_cpu_mask, spdk_mem):
    nodes = node_docker.containers.list(all=True)
    for node in nodes:
        if node.attrs["Name"] == "/spdk":
            logger.info("spdk container found, skip deploy...")
            return
    container = node_docker.containers.run(
        constants.SIMPLY_BLOCK_SPDK_ULTRA_IMAGE,
        f"/root/scripts/run_distr.sh {spdk_cpu_mask} {spdk_mem}",
        detach=True,
        privileged=True,
        name="spdk",
        network_mode="host",
        volumes=[
            '/var/tmp:/var/tmp',
            '/dev:/dev',
            '/lib/modules/:/lib/modules/',
            '/sys:/sys'],
        restart_policy={"Name": "on-failure", "MaximumRetryCount": 99}
    )
    container2 = node_docker.containers.run(
        constants.SIMPLY_BLOCK_SPDK_ULTRA_IMAGE,
        "python /root/scripts/spdk_http_proxy.py",
        name="spdk_proxy",
        detach=True,
        network_mode="host",
        volumes=[
            '/var/tmp:/var/tmp',
            '/etc/foundationdb:/etc/foundationdb'],
        restart_policy={"Name": "on-failure", "MaximumRetryCount": 99}
    )
    retries = 10
    while retries > 0:
        info = node_docker.containers.get(container.attrs['Id'])
        status = info.attrs['State']["Status"]
        is_running = info.attrs['State']["Running"]
        if not is_running:
            logger.info("Container is not running, waiting...")
            time.sleep(3)
            retries -= 1
        else:
            logger.info(f"Container status: {status}, Is Running: {is_running}")
            break


def join_cluster(cluster_ip, cluster_id, role, ifname, data_nics,  spdk_cpu_mask, spdk_mem):  # role: ["management", "storage"]

    if role not in ["management", "storage", "storage-alloc"]:
        logger.error(f"Unknown role: {role}")
        return False

    try:
        resp = requests.get(f"http://{cluster_ip}/cluster/{cluster_id}")
        resp_json = resp.json()
        cluster_data = resp_json['results'][0]
        logger.info(f"Cluster found! NQN:{cluster_data['nqn']}")
        logger.debug(cluster_data)
    except Exception as e:
        logger.error("Error getting cluster data!")
        logger.error(e)
        return ""

    logger.info("Installing dependencies...")
    ret = scripts.install_deps()
    logger.info("Installing dependencies > Done")

    if not ifname:
        ifname = "eth0"

    DEV_IP = utils.get_iface_ip(ifname)
    if not DEV_IP:
        logger.error(f"Error getting interface ip: {ifname}")
        return False

    logger.info(f"Node IP: {DEV_IP}")
    ret = scripts.configure_docker(DEV_IP)

    db_connection = cluster_data['db_connection']
    ret = scripts.set_db_config(db_connection)

    if role == "storage":
        logger.info("Deploying SPDK")
        node_cpu_count = os.cpu_count()
        if spdk_cpu_mask:
            requested_cpu_count = len(format(int(spdk_cpu_mask, 16), 'b'))
            if requested_cpu_count > node_cpu_count:
                logger.error(f"The requested cpu count: {requested_cpu_count} "
                             f"is larger than the node's cpu count: {node_cpu_count}")
                return False
        else:
            spdk_cpu_mask = hex(int(math.pow(2, node_cpu_count))-1)
        if spdk_mem:
            spdk_mem = int(spdk_mem/(1024*1024))
        else:
            spdk_mem = 4096
        node_docker = docker.DockerClient(base_url=f"tcp://{DEV_IP}:2375", version="auto", timeout=60*5)
        deploy_spdk(node_docker, spdk_cpu_mask, spdk_mem)
        time.sleep(5)

    logger.info("Joining docker swarm...")
    db_controller = DBController()
    nodes = db_controller.get_mgmt_nodes(cluster_id=cluster_id)
    if not nodes:
        logger.error("No mgmt nodes was found in the cluster!")
        exit(1)

    try:
        cluster_docker = utils.get_docker_client(cluster_id)
        docker_ip = cluster_docker.info()["Swarm"]["NodeAddr"]

        if role == 'management':
            join_token = cluster_docker.swarm.attrs['JoinTokens']['Manager']
        else:
            join_token = cluster_docker.swarm.attrs['JoinTokens']['Worker']

        node_docker = docker.DockerClient(base_url=f"tcp://{DEV_IP}:2375", version="auto")
        if node_docker.info()["Swarm"]["LocalNodeState"] == "active":
            logger.info("Node is part of another swarm, leaving swarm")
            try:
                cluster_docker.nodes.get(node_docker.info()["Swarm"]["NodeID"]).remove(force=True)
            except:
                pass
            node_docker.swarm.leave(force=True)
            time.sleep(5)
        node_docker.swarm.join([f"{docker_ip}:2377"], join_token)

        retries = 10
        while retries > 0:
            if node_docker.info()["Swarm"]["LocalNodeState"] == "active":
                break
            logger.info("Waiting for node to be active...")
            retries -= 1
            time.sleep(2)
        logger.info("Joining docker swarm > Done")
        time.sleep(5)

    except Exception as e:
        raise e

    if role == 'management':
        mgmt_node_ops.add_mgmt_node(DEV_IP, cluster_id)
        cluster_obj = db_controller.get_clusters(cluster_id)[0]
        nodes = db_controller.get_mgmt_nodes(cluster_id=cluster_id)
        if len(nodes) >= 3:
            logger.info("Waiting for FDB container to be active...")
            fdb_cont = None
            retries = 30
            while retries > 0 and fdb_cont is None:
                logger.info("Looking for FDB container...")
                for cont in node_docker.containers.list(all=True):
                    logger.debug(cont.attrs['Name'])
                    if cont.attrs['Name'].startswith("/app_fdb"):
                        fdb_cont = cont
                        break
                if fdb_cont:
                    logger.info("FDB container found")
                    break
                else:
                    retries -= 1
                    time.sleep(5)

            if not fdb_cont:
                logger.warning("FDB container was not found")
            else:
                retries = 10
                while retries > 0:
                    info = node_docker.containers.get(fdb_cont.attrs['Id'])
                    status = info.attrs['State']["Status"]
                    is_running = info.attrs['State']["Running"]
                    if not is_running:
                        logger.info("Container is not running, waiting...")
                        time.sleep(3)
                        retries -= 1
                    else:
                        logger.info(f"Container status: {status}, Is Running: {is_running}")
                    break

            logger.info("Configuring Double DB...")
            time.sleep(3)
            out = scripts.set_db_config_double()
            cluster_obj.ha_type = "ha"
            cluster_obj.write_to_db(db_controller.kv_store)
            logger.info("Configuring DB > Done")

    elif role == "storage":
        # add storage node
        fdb_cont = None
        retries = 30
        while retries > 0 and fdb_cont is None:
            logger.info("Looking for SpdkAppProxy container...")
            for cont in node_docker.containers.list(all=True):
                logger.debug(cont.attrs['Name'])
                if cont.attrs['Name'].startswith("/app_SpdkAppProxy"):
                    fdb_cont = cont
                    break
            if fdb_cont:
                logger.info("SpdkAppProxy container found")
                break
            else:
                retries -= 1
                time.sleep(5)

        if not fdb_cont:
            logger.warning("SpdkAppProxy container was not found")
        else:
            retries = 10
            while retries > 0:
                info = node_docker.containers.get(fdb_cont.attrs['Id'])
                status = info.attrs['State']["Status"]
                is_running = info.attrs['State']["Running"]
                if not is_running:
                    logger.info("Container is not running, waiting...")
                    time.sleep(3)
                    retries -= 1
                else:
                    logger.info(f"Container status: {status}, Is Running: {is_running}")
                break
        storage_node_ops.add_storage_node(cluster_id, ifname, data_nics)

    logger.info("Node joined the cluster")


def add_cluster(blk_size, page_size_in_blocks, model_ids, ha_type, tls,
                auth_hosts_only, dhchap, nqn, iscsi, cli_pass):
    db_controller = DBController()
    logger.info("Adding new cluster")
    c = Cluster()
    c.uuid = str(uuid.uuid4())
    c.blk_size = blk_size
    c.page_size_in_blocks = page_size_in_blocks
    c.model_ids = model_ids
    c.ha_type = ha_type
    c.tls = tls
    c.auth_hosts_only = auth_hosts_only
    c.nqn = nqn
    c.iscsi = iscsi
    c.dhchap = dhchap
    c.cli_pass = cli_pass
    c.status = Cluster.STATUS_ACTIVE
    c.updated_at = int(time.time())
    c.write_to_db(db_controller.kv_store)
    logger.info("New Cluster has been created")
    logger.info(c.uuid)


def show_cluster(cl_id, is_json=False):
    db_controller = DBController()
    cls = db_controller.get_clusters(id=cl_id)
    if not cls:
        logger.error(f"Cluster not found {cl_id}")
        return False

    cluster = cls[0]
    st = db_controller.get_storage_nodes()
    data = []
    for node in st:
        for dev in node.nvme_devices:
            data.append({
                "UUID": dev.get_id(),
                "Storage ID": dev.cluster_device_order,
                "Size": utils.humanbytes(dev.size),
                "Hostname": node.hostname,
                "Status": dev.status
            })
    data = sorted(data, key=lambda x: x["Storage ID"])
    if is_json:
        return json.dumps(data, indent=2)
    else:
        return utils.print_table(data)


def suspend_cluster(cl_id):
    db_controller = DBController()
    cls = db_controller.get_clusters(id=cl_id)
    if not cls:
        logger.error(f"Cluster not found {cl_id}")
        return False
    cl = cls[0]
    old_status = cl.status
    cl.status = Cluster.STATUS_SUSPENDED
    cl.write_to_db(db_controller.kv_store)

    cluster_events.cluster_status_change(cl, cl.status, old_status)
    return "Done"


def unsuspend_cluster(cl_id):
    db_controller = DBController()
    cls = db_controller.get_clusters(id=cl_id)
    if not cls:
        logger.error(f"Cluster not found {cl_id}")
        return False
    cl = cls[0]
    old_status = cl.status
    cl.status = Cluster.STATUS_ACTIVE
    cl.write_to_db(db_controller.kv_store)

    cluster_events.cluster_status_change(cl, cl.status, old_status)
    return "Done"


def degrade_cluster(cl_id):
    db_controller = DBController()
    cls = db_controller.get_clusters(id=cl_id)
    if not cls:
        logger.error(f"Cluster not found {cl_id}")
        return False
    cl = cls[0]
    old_status = cl.status
    cl.status = Cluster.STATUS_DEGRADED
    cl.write_to_db(db_controller.kv_store)

    cluster_events.cluster_status_change(cl, cl.status, old_status)
    return "Done"


def list():
    db_controller = DBController()
    cls = db_controller.get_clusters()
    st = db_controller.get_storage_nodes()
    mt = db_controller.get_mgmt_nodes()

    data = []
    for cl in cls:
        data.append({
            "UUID": cl.id,
            "NQN": cl.nqn,
            "ha_type": cl.ha_type,
            "tls": cl.tls,
            "mgmt nodes": len(mt),
            "storage nodes": len(st),
            "Status": cl.status,
        })
    return utils.print_table(data)


def get_capacity(cluster_id, history, records_count=20, parse_sizes=True):
    db_controller = DBController()
    cls = db_controller.get_clusters(id=cluster_id)
    if not cls:
        logger.error(f"Cluster not found {cluster_id}")
        return False
    cl = cls[0]

    if history:
        records_number = utils.parse_history_param(history)
        if not records_number:
            logger.error(f"Error parsing history string: {history}")
            return False
    else:
        records_number = 20

    records = db_controller.get_cluster_capacity(cl, records_number)

    new_records = utils.process_records(records, records_count)

    if not parse_sizes:
        return new_records

    out = []
    for record in new_records:
        out.append({
            "Date": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(record['date'])),
            "Absolut": utils.humanbytes(record['size_total']),
            "Provisioned": utils.humanbytes(record['size_prov']),
            "Used": utils.humanbytes(record['size_used']),
            "Free": utils.humanbytes(record['size_free']),
            "Util %": f"{record['size_util']}%",
            "Prov Util %": f"{record['size_prov_util']}%",
        })
    return out


def get_iostats_history(cluster_id, history_string, records_count=20, parse_sizes=True):
    db_controller = DBController()
    cls = db_controller.get_clusters(id=cluster_id)
    if not cls:
        logger.error(f"Cluster not found {cluster_id}")
        return False
    cl = cls[0]

    nodes = db_controller.get_storage_nodes_by_cluster_id(cluster_id)
    if not nodes:
        logger.error("no nodes found")
        return False

    if history_string:
        records_number = utils.parse_history_param(history_string)
        if not records_number:
            logger.error(f"Error parsing history string: {history_string}")
            return False
    else:
        records_number = 20

    records = db_controller.get_cluster_stats(cluster_id, records_number)

    # combine records
    new_records = utils.process_records(records, records_count)

    if not parse_sizes:
        return new_records

    out = []
    for record in new_records:
        out.append({
            "Date": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(record['date'])),
            "Read speed": utils.humanbytes(record['read_bytes_per_sec']),
            "Read IOPS": record["read_iops"],
            "Read lat": record["read_latency_ticks"],
            "Write speed":  utils.humanbytes(record["write_bytes_per_sec"]),
            "Write IOPS": record["write_iops"],
            "Write lat": record["write_latency_ticks"],
        })
    return out


def get_ssh_pass(cluster_id):
    db_controller = DBController()
    cls = db_controller.get_clusters(id=cluster_id)
    if not cls:
        logger.error(f"Cluster not found {cluster_id}")
        return False
    cl = cls[0]
    return cl.cli_pass


def get_secret(cluster_id):
    db_controller = DBController()
    cls = db_controller.get_clusters(id=cluster_id)
    if not cls:
        logger.error(f"Cluster not found {cluster_id}")
        return False
    cl = cls[0]
    return cl.secret


def set_secret(cluster_id, secret):
    db_controller = DBController()
    cls = db_controller.get_clusters(cluster_id)
    if not cls:
        logger.error(f"Cluster not found {cluster_id}")
        return False
    cl = cls[0]

    secret = secret.strip()
    if len(secret) < 20:
        return "Secret must be at least 20 char"

    cl.secret = secret
    cl.write_to_db(db_controller.kv_store)
    return "Done"


def get_logs(cluster_id, is_json=False):
    db_controller = DBController()
    cls = db_controller.get_clusters(cluster_id)
    if not cls:
        logger.error(f"Cluster not found {cluster_id}")
        return False
    cl = cls[0]

    events = db_controller.get_events(cluster_id)
    out = []
    for record in events:
        logger.debug(record)
        Storage_ID = None
        if 'storage_ID' in record.object_dict:
            Storage_ID = record.object_dict['storage_ID']

        vuid = None
        if 'vuid' in record.object_dict:
            vuid = record.object_dict['vuid']

        out.append({
            "Date": time.strftime("%H:%M:%S, %d/%m/%Y", time.gmtime(record.date)),
            "NodeId": record.node_id,
            "Event": record.event,
            "Level": record.event_level,
            "Message": record.message,
            "Storage_ID": str(Storage_ID),
            "VUID": str(vuid),
            "Status": record.status,
        })
    if is_json:
        return json.dumps(out, indent=2)
    else:
        return utils.print_table(out)
