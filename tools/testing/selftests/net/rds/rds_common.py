# SPDX-License-Identifier: GPL-2.0

import os
import re

# run_test flag space
OP_FLAG_TCP     = 0x1
OP_FLAG_RDMA    = 0x2

# from include/uapi/linux/rds.h: SO_RDS_TRANSPORT pins a socket to a
# specific RDS transport so connection setup cannot silently fall back
# to another (e.g. loopback) transport.
SOL_RDS          = 276
SO_RDS_TRANSPORT = 8
RDS_TRANS_TCP    = 2
RDS_TRANS_IB     = 0

class RdsSkipEx(Exception):
    """Raise to skip a test (e.g. missing prerequisites)."""
    pass

def _load_settings():
    settings_path = os.path.join(os.path.dirname(__file__), "settings")
    result = {}
    with open(settings_path) as f:
        for line in f:
            m = re.match(r'^(\w+)=(.*)$', line.strip())
            if m:
                result[m.group(1)] = m.group(2)
    return result

KSFT_TIMEOUT = int(_load_settings().get("timeout", 0))

# Tests need to end before the ksft runner
# time out to allow time for log collection
TEST_TIMEOUT = KSFT_TIMEOUT - 30

all_tests = {
    "1.0": {
        "file": "001-basic.py",
        "tags": {"basic"},
        "transports": {"tcp", "rdma"},
        "timeout": TEST_TIMEOUT,
        "packet_loss": 0,
        "packet_corrupt": 0,
        "packet_duplicate": 0,
    },
}
