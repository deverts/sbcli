from e2e_tests.single_node_outage import TestSingleNodeOutage
from e2e_tests.single_node_failure import TestSingleNodeFailure
from e2e_tests.single_node_multi_fio_perf import (
    TestLvolFioNpcs0, TestLvolFioNpcs1, TestLvolFioNpcs2, TestLvolFioNpcsCustom
)
from e2e_tests.multi_lvol_run_fio import TestManyLvolSameNode
from e2e_tests.batch_lvol_limit import TestBatchLVOLsLimit
from e2e_tests.cloning_and_snapshot.multi_lvol_snapshot_fio import TestMultiLvolFio
from e2e_tests.ha_journal.lvol_journal_device_node_restart import TestDeviceNodeRestart



def get_all_tests(custom=True, k8s_test=False):
    tests = [
        # TestSingleNodeOutage,
        # TestLvolFioNpcsCustom,
        # TestLvolFioNpcs0,
        # TestLvolFioNpcs1,
        # TestLvolFioNpcs2,
        TestSingleNodeFailure
        # TestManyLvolSameNode,
        # TestBatchLVOLsLimit,

        # Enable when testing snapshot and cloning

        # TestMultiLvolFio,
        # TestSnapshotBatchCloneLVOLs,
        # TestManyClonesFromSameSnapshot,
        # TestDeviceNodeRestart
    ]
    # if not custom:
    #     tests.remove(TestLvolFioNpcsCustom)
    # else:
    #     tests.remove(TestLvolFioNpcs0)
    #     tests.remove(TestLvolFioNpcs1)
    #     tests.remove(TestLvolFioNpcs2)
    return tests
