# coding=utf-8
import time
from datetime import datetime


from simplyblock_core.controllers import health_controller, storage_events, device_events
from simplyblock_core.models.cluster import Cluster
from simplyblock_core.models.nvme_device import NVMeDevice
from simplyblock_core.models.storage_node import StorageNode
from simplyblock_core.rpc_client import RPCClient
from simplyblock_core import constants, db_controller, utils, distr_controller
from simplyblock_core.snode_client import SNodeClient

logger = utils.get_logger(__name__)


def set_node_health_check(snode, health_check_status):
    snode = db_controller.get_storage_node_by_id(snode.get_id())
    if snode.health_check == health_check_status:
        return
    old_status = snode.health_check
    snode.health_check = health_check_status
    snode.updated_at = str(datetime.now())
    snode.write_to_db()
    storage_events.snode_health_check_change(snode, snode.health_check, old_status, caused_by="monitor")


def set_device_health_check(cluster_id, device, health_check_status):
    if device.health_check == health_check_status:
        return
    nodes = db_controller.get_storage_nodes_by_cluster_id(cluster_id)
    for node in nodes:
        if node.nvme_devices:
            for dev in node.nvme_devices:
                if dev.get_id() == device.get_id():
                    old_status = dev.health_check
                    dev.health_check = health_check_status
                    node.write_to_db()
                    device_events.device_health_check_change(
                        dev, dev.health_check, old_status, caused_by="monitor")
                    return


# get DB controller
db_controller = db_controller.DBController()

nodes_ports_blocked = {}

logger.info("Starting health check service")
while True:
    clusters = db_controller.get_clusters()
    for cluster in clusters:
        cluster_id = cluster.get_id()
        snodes = db_controller.get_storage_nodes_by_cluster_id(cluster_id)
        if not snodes:
            logger.error("storage nodes list is empty")

        for snode in snodes:
            logger.info("Node: %s, status %s", snode.get_id(), snode.status)

            if snode.status not in [StorageNode.STATUS_ONLINE, StorageNode.STATUS_UNREACHABLE,
                                    StorageNode.STATUS_SUSPENDED, StorageNode.STATUS_DOWN]:
                logger.info(f"Node status is: {snode.status}, skipping")
                set_node_health_check(snode, False)

            # 1- check node ping
            ping_check = health_controller._check_node_ping(snode.mgmt_ip)
            logger.info(f"Check: ping mgmt ip {snode.mgmt_ip} ... {ping_check}")

            # 2- check node API
            node_api_check = health_controller._check_node_api(snode.mgmt_ip)
            logger.info(f"Check: node API {snode.mgmt_ip}:5000 ... {node_api_check}")

            # 3- check node RPC
            node_rpc_check = health_controller._check_node_rpc(
                snode.mgmt_ip, snode.rpc_port, snode.rpc_username, snode.rpc_password)
            logger.info(f"Check: node RPC {snode.mgmt_ip}:{snode.rpc_port} ... {node_rpc_check}")

            # 4- docker API
            node_docker_check = health_controller._check_node_docker_api(snode.mgmt_ip)
            logger.info(f"Check: node docker API {snode.mgmt_ip}:2375 ... {node_docker_check}")

            is_node_online = ping_check and node_api_check and node_rpc_check and node_docker_check

            health_check_status = is_node_online
            if not node_rpc_check:
                for dev in snode.nvme_devices:
                    if dev.io_error:
                        logger.debug(f"Skipping Device action because of io_error {dev.get_id()}")
                        continue
                    set_device_health_check(cluster_id, dev, False)
            else:
                logger.info(f"Node device count: {len(snode.nvme_devices)}")
                node_devices_check = True
                node_remote_devices_check = True


                rpc_client = RPCClient(
                    snode.mgmt_ip, snode.rpc_port,
                    snode.rpc_username, snode.rpc_password,
                    timeout=3, retry=2)
                connected_devices = []

                node_bdevs = rpc_client.get_bdevs()
                if node_bdevs:
                    node_bdev_names = [b['name'] for b in node_bdevs]
                else:
                    node_bdev_names = []

                subsystem_list = rpc_client.subsystem_list()
                if subsystem_list:
                    subsystem_list = [item['nqn'] for item in subsystem_list]

                for device in snode.nvme_devices:
                    if device.io_error:
                        logger.debug(f"Skipping Device check because of io_error {device.get_id()}")
                        continue
                    # ret = health_controller.check_device(dev.get_id())
                    passed = True
                    if snode.enable_test_device:
                        bdevs_stack = [device.nvme_bdev, device.testing_bdev, device.alceml_bdev, device.pt_bdev]
                    else:
                        bdevs_stack = [device.nvme_bdev, device.alceml_bdev, device.pt_bdev]

                    logger.info(f"Checking Device: {device.get_id()}, status:{device.status}")
                    problems = 0
                    for bdev in bdevs_stack:
                        if not bdev:
                            continue
                        # ret = rpc_client.get_bdevs(bdev)
                        if bdev in node_bdev_names:
                            logger.debug(f"Checking bdev: {bdev} ... ok")
                        else:
                            logger.error(f"Checking bdev: {bdev} ... not found")
                            problems += 1
                            passed = False
                            # return False
                    logger.info(f"Checking Device's BDevs ... ({(len(bdevs_stack) - problems)}/{len(bdevs_stack)})")

                    # ret = rpc_client.subsystem_list(device.nvmf_nqn)
                    logger.debug(f"Checking subsystem: {device.nvmf_nqn}")
                    if device.nvmf_nqn in subsystem_list:
                        logger.info(f"Checking subsystem ... ok")
                    else:
                        logger.info(f"Checking subsystem: ... not found")
                        passed = False

                    set_device_health_check(cluster_id, device, passed)
                    if device.status == NVMeDevice.STATUS_ONLINE:
                        node_devices_check &= passed

                logger.info(f"Node remote device: {len(snode.remote_devices)}")

                for remote_device in snode.remote_devices:
                    org_dev = db_controller.get_storage_device_by_id(remote_device.get_id())
                    org_node =  db_controller.get_storage_node_by_id(remote_device.node_id)
                    if org_dev.status == NVMeDevice.STATUS_ONLINE and org_node.status == StorageNode.STATUS_ONLINE:
                        # ret = rpc_client.get_bdevs(remote_device.remote_bdev)
                        if remote_device.remote_bdev in node_bdev_names:
                            logger.info(f"Checking bdev: {remote_device.remote_bdev} ... ok")
                            connected_devices.append(remote_device.get_id())
                            ret = True
                        else:
                            logger.info(f"Checking bdev: {remote_device.remote_bdev} ... not found")
                            if not org_dev.alceml_bdev:
                                logger.error(f"device alceml bdev not found!, {org_dev.get_id()}")
                                continue
                            name = f"remote_{org_dev.alceml_bdev}"
                            # if rpc_client.bdev_nvme_controller_list(name):
                            #     logger.info(f"detaching {name} from {snode.get_id()}")
                            #     rpc_client.bdev_nvme_detach_controller(name)
                            #     time.sleep(1)

                            logger.info(f"Connecting {name} to {snode.get_id()}")
                            ret = rpc_client.bdev_nvme_attach_controller_tcp(
                                name, org_dev.nvmf_nqn, org_dev.nvmf_ip, org_dev.nvmf_port)
                            if ret:
                                logger.info(f"Successfully connected to device: {org_dev.get_id()}")
                                connected_devices.append(org_dev.get_id())
                                sn = db_controller.get_storage_node_by_id(snode.get_id())
                                for d in sn.remote_devices:
                                    if d.get_id() == remote_device.get_id():
                                        d.status = NVMeDevice.STATUS_ONLINE
                                        sn.write_to_db()
                                        break

                                distr_controller.send_dev_status_event(org_dev, NVMeDevice.STATUS_ONLINE, snode)
                            else:
                                logger.error(f"Failed to connect to device: {org_dev.get_id()}")

                        node_remote_devices_check &= bool(ret)

                # for node in db_controller.get_storage_nodes_by_cluster_id(snode.cluster_id):
                #     if node.status != StorageNode.STATUS_ONLINE or node.get_id() == snode.get_id():
                #         continue
                #     for dev in node.nvme_devices:
                #         if dev.status == NVMeDevice.STATUS_ONLINE:
                #             if dev.get_id() not in connected_devices:
                #                 if not dev.alceml_bdev:
                #                     logger.error(f"device alceml bdev not found!, {dev.get_id()}")
                #                     continue
                #                 logger.info(f"connecting to online device: {dev.get_id()}")
                #                 name = f"remote_{dev.alceml_bdev}"
                #                 bdev_name = f"{name}n1"
                #                 if rpc_client.bdev_nvme_controller_list(name):
                #                     logger.info(f"detaching {name} from {snode.get_id()}")
                #                     rpc_client.bdev_nvme_detach_controller(name)
                #                     time.sleep(1)
                #
                #                 logger.info(f"Connecting {name} to {snode.get_id()}")
                #                 ret = rpc_client.bdev_nvme_attach_controller_tcp(
                #                     name, dev.nvmf_nqn, dev.nvmf_ip,
                #                     dev.nvmf_port)
                #                 if ret:
                #                     logger.info(f"Successfully connected to device: {dev.get_id()}")
                #                     dev.remote_bdev = bdev_name
                #                     snode = db_controller.get_storage_node_by_id(snode.get_id())
                #                     snode.remote_devices.append(dev)
                #                     snode.write_to_db()
                #                     distr_controller.send_dev_status_event(dev, NVMeDevice.STATUS_ONLINE, snode)
                #                 else:
                #                     logger.error(f"Failed to connect to device: {dev.get_id()}")

                online_jms = 0
                if snode.jm_device and snode.jm_device.get_id():
                    jm_device = snode.jm_device
                    logger.info(f"Node JM: {jm_device.get_id()}")
                    if jm_device.jm_bdev in node_bdev_names:
                        logger.info(f"Checking jm bdev: {jm_device.jm_bdev} ... ok")
                        online_jms += 1
                    else:
                        logger.info(f"Checking jm bdev: {jm_device.jm_bdev} ... not found")

                    # node_devices_check &= ret

                if snode.enable_ha_jm:
                    logger.info(f"Node remote JMs: {len(snode.remote_jm_devices)}")
                    for remote_device in snode.remote_jm_devices:
                        # ret = rpc_client.get_bdevs(remote_device.remote_bdev)
                        if remote_device.remote_bdev in node_bdev_names:
                            logger.info(f"Checking bdev: {remote_device.remote_bdev} ... ok")
                            online_jms += 1
                        else:
                            logger.info(f"Checking bdev: {remote_device.remote_bdev} ... not found")

                            if cluster.status not in [Cluster.STATUS_ACTIVE, Cluster.STATUS_DEGRADED, Cluster.STATUS_READONLY]:
                                continue
                            org_dev = None
                            org_dev_node = None
                            for node in db_controller.get_storage_nodes():
                                if node.jm_device and node.jm_device.get_id() == remote_device.get_id():
                                    org_dev = node.jm_device
                                    org_dev_node = node
                                    break

                            if org_dev and org_dev.status == NVMeDevice.STATUS_ONLINE and \
                                    org_dev_node.status == StorageNode.STATUS_ONLINE:
                                name = f"remote_{remote_device.jm_bdev}"
                                ret = rpc_client.bdev_nvme_attach_controller_tcp(
                                    name, remote_device.nvmf_nqn, remote_device.nvmf_ip,
                                    remote_device.nvmf_port)
                                if ret:
                                    logger.info(f"Successfully connected to jm device: {remote_device.get_id()}")
                                    online_jms += 1
                                else:
                                    logger.error(f"Failed to connect to jm device: {remote_device.get_id()}")
                            else:
                                continue

                    if online_jms < 2:
                        node_remote_devices_check = False
                else:
                    if online_jms == 0:
                        node_remote_devices_check = False


                lvstore_check = True
                if snode.lvstore_stack:
                    lvstore_stack = snode.lvstore_stack
                    lvstore_check &= health_controller._check_node_lvstore(lvstore_stack, snode, auto_fix=True)
                    if snode.secondary_node_id:
                        second_node_1 = db_controller.get_storage_node_by_id(snode.secondary_node_id)
                        if second_node_1 and second_node_1.status == StorageNode.STATUS_ONLINE:
                            lvstore_check &= health_controller._check_node_lvstore(lvstore_stack, second_node_1, auto_fix=True)

                    lvol_port_check = False
                    if node_api_check:
                        lvol_port_check = health_controller._check_port_on_node(snode, snode.lvol_subsys_port)
                        logger.info(
                            f"Check: node {snode.mgmt_ip}:{snode.rpc_port}, port: {snode.lvol_subsys_port} ... {lvol_port_check}")
                        if not lvol_port_check:
                            if snode.get_id() in nodes_ports_blocked:
                                nodes_ports_blocked[snode.get_id()].append(snode.lvol_subsys_port)
                            else:
                                nodes_ports_blocked[snode.get_id()] = [snode.lvol_subsys_port]


                if snode.is_secondary_node:
                    for node in db_controller.get_storage_nodes():
                        if node.secondary_node_id == snode.get_id() and node.status == StorageNode.STATUS_ONLINE:
                            logger.info(f"Checking stack from node : {node.get_id()}")
                            lvstore_check &= health_controller._check_node_lvstore(node.lvstore_stack, snode, auto_fix=True)
                            lvol_port_check = False
                            if node_api_check:
                                lvol_port_check = health_controller._check_port_on_node(snode, node.lvol_subsys_port)
                                logger.info(
                                    f"Check: node {snode.mgmt_ip}:{snode.rpc_port}, port: {node.lvol_subsys_port} ... {lvol_port_check}")
                                if not lvol_port_check:
                                    if snode.get_id() in nodes_ports_blocked:
                                        nodes_ports_blocked[snode.get_id()].append(node.lvol_subsys_port)
                                    else:
                                        nodes_ports_blocked[snode.get_id()] = [node.lvol_subsys_port]

                health_check_status = is_node_online and node_devices_check and node_remote_devices_check and lvstore_check
            set_node_health_check(snode, health_check_status)

    time.sleep(constants.HEALTH_CHECK_INTERVAL_SEC)

    for node_id in nodes_ports_blocked:
        snode = db_controller.get_storage_node_by_id(node_id)
        snode_api = SNodeClient(f"{snode.mgmt_ip}:5000", timeout=3, retry=2)
        if nodes_ports_blocked[node_id]:
            for port in nodes_ports_blocked[node_id]:
                if port:
                    logger.info(f"Allow port {port} on node {node_id}")
                    snode_api.firewall_set_port(port, "tcp", "allow")

    nodes_ports_blocked = {}

