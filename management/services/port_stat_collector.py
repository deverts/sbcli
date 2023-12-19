# coding=utf-8
import logging
import os

import time
import sys

import psutil

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(SCRIPT_PATH, "../.."))

from management import constants, kv_store, utils
from management.models.device_stat import PortStat


def update_port_stats(snode, nic, stats):
    now = int(time.time())
    data = {
        "uuid": nic.get_id(),
        "node_id": snode.get_id(),
        "date": now,
        "bytes_sent": stats.bytes_sent,
        "bytes_received": stats.bytes_recv,
        "packets_sent": stats.packets_sent,
        "packets_received": stats.packets_recv,
        "errin": stats.errin,
        "errout": stats.errout,
        "dropin": stats.dropin,
        "dropout": stats.dropout,
    }
    last_stat = PortStat(data={"uuid": nic.get_id(), "node_id": snode.get_id()}).get_last(db_controller.kv_store)
    if last_stat:
        data.update({
            "out_speed": int((stats.bytes_sent - last_stat.bytes_sent) / (now - last_stat.date)),
            "in_speed": int((stats.bytes_recv - last_stat.bytes_received) / (now - last_stat.date)),
        })
    stat_obj = PortStat(data=data)
    stat_obj.write_to_db(db_controller.kv_store)
    return


# configure logging
logger_handler = logging.StreamHandler(stream=sys.stdout)
logger_handler.setFormatter(logging.Formatter('%(asctime)s: %(levelname)s: %(message)s'))
logger = logging.getLogger()
logger.addHandler(logger_handler)
logger.setLevel(logging.DEBUG)

# get DB controller
db_controller = kv_store.DBController()

hostname = utils.get_hostname()
logger.info("Starting port stats collector...")
while True:
    time.sleep(constants.PROT_STAT_COLLECTOR_INTERVAL_SEC)

    snode = db_controller.get_storage_node_by_hostname(hostname)
    if not snode:
        logger.error("This node is not part of the cluster, hostname: %s" % hostname)
        continue

    if not snode.data_nics:
        logger.error("No Data Ports found in node: %s", snode.get_id())
        continue

    io_stats = psutil.net_io_counters(pernic=True)
    for nic in snode.data_nics:
        logger.info("Getting port stats: %s", nic.get_id())
        if nic.if_name in io_stats:
            stats = io_stats[nic.if_name]
            update_port_stats(snode, nic, stats)
        else:
            logger.error("Error getting port stats: %s", nic.get_id())

