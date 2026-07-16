#! /usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import shutil
import subprocess
import sys
import time
from rds_common import *

# Allow utils module to be imported from different directory
this_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(this_dir, "../"))
# pylint: disable-next=wrong-import-position,import-error,no-name-in-module
from lib.py.ksft import ksft_pr

# Test metadata — consumed by the test.py dispatcher.
NAME = "stress"
TAGS = {"stress"}
TRANSPORTS = {"tcp", "rdma"}


def _ns_prefix(ns):
    return ['ip', 'netns', 'exec', ns] if ns else []


def _run_stress(addr0, addr1, port, duration, ns0=None, ns1=None):
    nr_tasks = 1
    q_depth = 1

    p0 = subprocess.Popen(_ns_prefix(ns0) + [
        'rds-stress',
        '-r', addr0,
        '-p', str(port),
        '-t', str(nr_tasks),
        '-d', str(q_depth),
        '-T', str(duration + 5),
    ])

    time.sleep(1)  # allow server to start listening

    p1 = subprocess.Popen(_ns_prefix(ns1) + [
        'rds-stress',
        '-r', addr1, '-s', addr0,
        '-p', str(port),
        '-t', str(nr_tasks),
        '-d', str(q_depth),
        '-T', str(duration),
    ])

    rc1 = p1.wait()
    rc0 = p0.wait()

    if rc0 != 0 or rc1 != 0:
        ksft_pr(f"rds-stress failed: server={rc0} client={rc1}")
        return 1

    ksft_pr("Success")
    return 0


def run_test(env):
    """Run RDS stress selftest over the configured transport.

    env is a dictionary provided by test.py and is expected to contain:
      - 'addrs':  list of (ip, port) tuples for the endpoints
      - 'netns':  list of network namespace names [net0, net1] or None for RDMA
      - 'flags':  op flags (e.g. OP_FLAG_RDMA)
    """
    if shutil.which('rds-stress') is None:
        raise RdsSkipEx("rds-stress not found in PATH; install rds-tools")

    addrs = env['addrs']
    netns = env['netns']
    flags = env['flags']

    time_out = 60

    if flags & OP_FLAG_RDMA:
        return _run_stress(addrs[0][0], addrs[1][0], addrs[0][1], time_out)
    return _run_stress(addrs[0][0], addrs[1][0], addrs[0][1], time_out,
                       ns0=netns[0], ns1=netns[1])
