#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import os
import subprocess
import sys
import time

# Allow utils module to be imported from different directory
this_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(this_dir, "../"))
from lib.py.utils import ip

RDMA_ADDR0 = '10.0.2.1'
RDMA_ADDR1 = '10.0.2.2'
VETH_RDMA0 = 'veth_rdma0'
VETH_RDMA1 = 'veth_rdma1'
RXE_DEV0 = 'rxe0'
RXE_DEV1 = 'rxe1'


def _rdma(cmd, ignore_error=False):
    fn = subprocess.call if ignore_error else subprocess.check_call
    fn(['rdma'] + cmd.split())


def _have_rxe():
    """Return True if the rdma_rxe (SoftRoCE) driver is available.

    Checks both the loadable module case (modinfo) and the built-in case
    (CONFIG_MODULES disabled).
    """
    if subprocess.call(
        ['modinfo', 'rdma_rxe'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ) == 0:
        return True
    # When CONFIG_MODULES is disabled, rdma_rxe may be built-in.
    # The rdma link add command will fail later if it's truly absent.
    return os.path.exists('/sys/bus/pci/drivers/rdma_rxe') or \
        os.path.exists('/sys/module/rdma_rxe')


def _teardown_rxe():
    """Remove SoftRoCE devices and the veth pair used for the RDMA test."""
    _rdma(f"link del {RXE_DEV0}", ignore_error=True)
    _rdma(f"link del {RXE_DEV1}", ignore_error=True)
    subprocess.call(['ip', 'link', 'del', VETH_RDMA0],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _setup_rxe():
    """Set up SoftRoCE (rdma_rxe) devices over a dedicated veth pair.

    Two RXE RDMA devices are created:
      RXE_DEV0 on VETH_RDMA0 (IP RDMA_ADDR0)
      RXE_DEV1 on VETH_RDMA1 (IP RDMA_ADDR1)

    Because RDMA_ADDR0 and RDMA_ADDR1 are non-loopback IPs on different
    interfaces, and because the RDS IB transport has no t_prefer_loopback,
    RDS will use the IB (RDMA) transport for connections between them even
    though both endpoints run on the same host.
    """
    # Clean up any stale state left by a previous interrupted run.
    _teardown_rxe()

    # modprobe may fail if rdma_rxe is built-in (CONFIG_MODULES disabled)
    subprocess.call(['modprobe', 'rdma_rxe'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ip(f"link add {VETH_RDMA0} type veth peer name {VETH_RDMA1}")
    ip(f"addr add {RDMA_ADDR0}/32 dev {VETH_RDMA0}")
    ip(f"addr add {RDMA_ADDR1}/32 dev {VETH_RDMA1}")
    ip(f"link set {VETH_RDMA0} up")
    ip(f"link set {VETH_RDMA1} up")
    ip(f"route add {RDMA_ADDR1}/32 dev {VETH_RDMA0}")
    ip(f"route add {RDMA_ADDR0}/32 dev {VETH_RDMA1}")

    _rdma(f"link add {RXE_DEV0} type rxe netdev {VETH_RDMA0}")
    _rdma(f"link add {RXE_DEV1} type rxe netdev {VETH_RDMA1}")

    time.sleep(1)  # allow RXE devices to initialise


def run_test(env):
    """Run RDS stress test over the RDMA (IB) transport using SoftRoCE.

    env is a dictionary provided by test.py and is expected to contain:
      - 'addrs':   list of (ip, port) tuples (unused; RDMA test uses its own)
      - 'netns':   list of network namespace names (unused; no namespaces needed)

    SoftRoCE (rdma_rxe) is configured over a fresh veth pair so that the
    RDS IB transport is exercised without requiring physical RDMA hardware.
    The test is skipped if rdma_rxe is unavailable or cannot be loaded.
    """
    if not _have_rxe():
        print("rds_rdma: rdma_rxe not available, skipping RDMA test")
        return 4  # KSFT_SKIP

    port = 30000
    nr_tasks = 1
    q_depth = 1
    duration = 60

    _setup_rxe()
    try:
        # Server: bound to RDMA_ADDR0 (via RXE_DEV0 / VETH_RDMA0)
        p0 = subprocess.Popen([
            'rds-stress',
            '-r', RDMA_ADDR0,
            '-p', str(port),
            '-t', str(nr_tasks),
            '-d', str(q_depth),
            '-T', str(duration + 5),
        ])

        time.sleep(1)  # allow server to start listening

        # Client: bound to RDMA_ADDR1 (via RXE_DEV1 / VETH_RDMA1),
        # connecting to the server at RDMA_ADDR0.
        p1 = subprocess.Popen([
            'rds-stress',
            '-r', RDMA_ADDR1, '-s', RDMA_ADDR0,
            '-p', str(port),
            '-t', str(nr_tasks),
            '-d', str(q_depth),
            '-T', str(duration),
        ])

        rc1 = p1.wait()
        rc0 = p0.wait()

        if rc0 != 0 or rc1 != 0:
            print(f"rds-stress (RDMA) failed: server={rc0} client={rc1}")
            return 1

        print("Success")
        return 0
    finally:
        _teardown_rxe()
