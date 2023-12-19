# coding=utf-8
import logging
import os

import time
import sys
from datetime import datetime


SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(SCRIPT_PATH, "../.."))

from management import constants, kv_store
from management.rpc_client import RPCClient
from management.models.caching_node import CachingNode

# configure logging
logger_handler = logging.StreamHandler(stream=sys.stdout)
logger_handler.setFormatter(logging.Formatter('%(asctime)s: %(levelname)s: %(message)s'))
logger = logging.getLogger()
logger.addHandler(logger_handler)
logger.setLevel(logging.DEBUG)


# get DB controller
db_store = kv_store.KVStore()
db_controller = kv_store.DBController(kv_store=db_store)


def set_node_online(node):
    if node.status == CachingNode.STATUS_UNREACHABLE:
        snode = db_controller.get_caching_node_by_id(node.get_id())
        old_status = snode.status
        snode.status = CachingNode.STATUS_ONLINE
        snode.updated_at = str(datetime.now())
        snode.write_to_db(db_store)
        # mgmt_events.status_change(snode, snode.status, old_status, caused_by="monitor")


def set_node_offline(node):
    if node.status == CachingNode.STATUS_ONLINE:
        snode = db_controller.get_caching_node_by_id(node.get_id())
        old_status = snode.status
        snode.status = CachingNode.STATUS_UNREACHABLE
        snode.updated_at = str(datetime.now())
        snode.write_to_db(db_store)
        # mgmt_events.status_change(snode, snode.status, old_status, caused_by="monitor")


def ping_host(ip):
    logger.info(f"Pinging ip {ip}")
    response = os.system(f"ping -c 1 {ip}")
    if response == 0:
        logger.info(f"{ip} is UP")
        return True
    else:
        logger.info(f"{ip} is DOWN")
        return False


logger.info("Starting Caching node monitor")


while True:
    nodes = db_controller.get_caching_nodes()
    for node in nodes:
        if node.status not in [CachingNode.STATUS_ONLINE, CachingNode.STATUS_UNREACHABLE]:
            logger.info(f"Node status is: {node.status}, skipping")
            continue

        logger.info(f"Checking node {node.hostname}")
        if not ping_host(node.mgmt_ip):
            logger.info(f"Node {node.hostname} is offline")
            set_node_offline(node)
            continue
        rpc_client = RPCClient(
            node.mgmt_ip,
            node.rpc_port,
            node.rpc_username,
            node.rpc_password,
            timeout=3, retry=3)
        try:
            logger.info(f"Calling rpc get_version...")
            response = rpc_client.get_version()
            if response:
                logger.info(f"Node {node.hostname} is online")
                set_node_online(node)
            else:
                logger.info(f"Node rpc {node.hostname} is unreachable")
                set_node_offline(node)
        except Exception as e:
            print(e)
            logger.info(f"Error connecting to node: {node.hostname}")
            set_node_offline(node)

    logger.info(f"Sleeping for {constants.NODE_MONITOR_INTERVAL_SEC} seconds")
    time.sleep(constants.NODE_MONITOR_INTERVAL_SEC)
