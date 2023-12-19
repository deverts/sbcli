# coding=utf-8

from datetime import datetime
from typing import List

from management.models.base_model import BaseModel
from management.models.iface import IFace
from management.models.lvol_model import LVol
from management.models.nvme_device import NVMeDevice


class CachedLVol(BaseModel):

    attributes = {
        "uuid": {"type": str, 'default': ""},
        "lvol_id": {"type": str, 'default': ""},
        "lvol": {"type": LVol, 'default': None},
        "hostname": {"type": str, 'default': ""},
        "local_nqn": {"type": str, 'default': ""},
        "ocf_bdev": {"type": str, 'default': ""},
        "device_path": {"type": str, 'default': ""},
    }

    def __init__(self, data=None):
        super(CachedLVol, self).__init__()
        self.set_attrs(self.attributes, data)
        self.object_type = "object"

    def get_id(self):
        return self.uuid


class CachingNode(BaseModel):

    STATUS_ONLINE = 'online'
    STATUS_OFFLINE = 'offline'
    STATUS_ERROR = 'error'
    STATUS_REPLACED = 'replaced'
    STATUS_SUSPENDED = 'suspended'
    STATUS_IN_CREATION = 'in_creation'
    STATUS_IN_SHUTDOWN = 'in_shutdown'
    STATUS_RESTARTING = 'restarting'
    STATUS_REMOVED = 'removed'
    STATUS_UNREACHABLE = 'unreachable'

    attributes = {
        "uuid": {"type": str, 'default': ""},
        "baseboard_sn": {"type": str, 'default': ""},
        "system_uuid": {"type": str, 'default': ""},
        "hostname": {"type": str, 'default': ""},
        "host_nqn": {"type": str, 'default': ""},
        "subsystem": {"type": str, 'default': ""},
        "nvme_devices": {"type": List[NVMeDevice], 'default': []},
        "sequential_number": {"type": int, 'default': 0},
        "partitions_count": {"type": int, 'default': 0},
        "ib_devices": {"type": List[IFace], 'default': []},
        "status": {"type": str, 'default': "in_creation"},
        "updated_at": {"type": str, 'default': str(datetime.now())},
        "create_dt": {"type": str, 'default': str(datetime.now())},
        "remove_dt": {"type": str, 'default': str(datetime.now())},
        "mgmt_ip": {"type": str, 'default': ""},
        "rpc_port": {"type": int, 'default': -1},
        "rpc_username": {"type": str, 'default': ""},
        "rpc_password": {"type": str, 'default': ""},
        "data_nics": {"type": List[IFace], 'default': []},
        "lvols": {"type": List[CachedLVol], 'default': []},
        "node_lvs": {"type": str, 'default': "lvs"},
        "services": {"type": List[str], 'default': []},
        "cluster_id": {"type": str, 'default': ""},
        "api_endpoint": {"type": str, 'default': ""},
        "remote_devices": {"type": List[NVMeDevice], 'default': []},
        "host_secret": {"type": str, "default": ""},
        "ctrl_secret": {"type": str, "default": ""},

        "cache_bdev": {"type": str, "default": ""},
        "cache_size": {"type": int, "default": 0},
        "cpu": {"type": int, "default": 0},
        "cpu_hz": {"type": int, "default": 0},
        "memory": {"type": int, "default": 0},
        "hugepages": {"type": int, "default": 0},

    }

    def __init__(self, data=None):
        super(CachingNode, self).__init__()
        self.set_attrs(self.attributes, data)
        self.object_type = "object"

    def get_id(self):
        return self.uuid
