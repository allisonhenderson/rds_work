# SPDX-License-Identifier: GPL-2.0

import atexit
import os
import re
import subprocess

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

def get_netns_inum_by_name(name) -> int:
    """Return the netns inode number (== RDS debugfs per-netns dir name)
    for a named netns.  'ip netns add <name>' bind-mounts the netns's nsfs
    node onto /run/netns/<name>, whose st_ino is the netns inum; name=None
    resolves to the caller's (host) netns, as used for the RDMA case.
    """
    path = f"/run/netns/{name}" if name else "/proc/self/ns/net"
    return os.stat(path).st_ino

def reset_proc(dbg_glob, interval) -> subprocess.Popen:
    """Periodically drop the RDS connection(s) whose debugfs reset file
    matches dbg_glob, forcing a reconnect.  dbg_glob is a shell glob
    resolving to one or more .../reset files.  Returns the running proc.
    """
    script = f"""
    while true; do
        for f in {dbg_glob}; do
            [ -f "$f" ] && echo 1 > "$f"
        done
        sleep {interval}
    done
    """
    return subprocess.Popen(["bash", "-c", script])

# Connection-reset processes started for the current test, so they can be
# torn down on completion or at exit.  Shared by the basic and stress tests.
reset_procs = []

def stop_reset_procs():
    """Stop any running connection-reset processes."""
    if not reset_procs:
        return

    print("# Stopping conn reset procs", flush=True)
    while reset_procs:
        proc = reset_procs.pop()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

def start_reset_procs(env, addrs, netns):
    """Start per-peer reset loops per the peer{1,2}_reset_interval values in
    env.  netns is [peer0_ns, peer1_ns] (names), or None for the RDMA case
    where both peers share the host netns.  The reset for peer0 targets the
    conn whose local address is addrs[0]; peer1 targets addrs[1].
    """
    flags = env.get('flags', 0)
    interval1 = env.get('peer1_reset_interval', 0)
    interval2 = env.get('peer2_reset_interval', 0)
    if interval1 <= 0 and interval2 <= 0:
        return

    trans = "tcp" if (flags & OP_FLAG_TCP) else "infiniband"
    dbg_root = "/sys/kernel/debug/rds"
    atexit.register(stop_reset_procs)

    if interval1 > 0:
        inum0 = get_netns_inum_by_name(netns[0] if netns else None)
        glob1 = f"{dbg_root}/{inum0}/*{addrs[0][0]}-*{addrs[1][0]}-{trans}-*/reset"
        reset_procs.append(reset_proc(glob1, interval1))

    if interval2 > 0:
        inum1 = get_netns_inum_by_name(netns[1] if netns else None)
        glob2 = f"{dbg_root}/{inum1}/*{addrs[1][0]}-*{addrs[0][0]}-{trans}-*/reset"
        reset_procs.append(reset_proc(glob2, interval2))


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
    "1.1": {
        "file": "001-basic.py",
        "tags": {"basic"},
        "transports": {"tcp", "rdma"},
        "timeout": TEST_TIMEOUT,
        "packet_loss": 0,
        "packet_corrupt": 0,
        "packet_duplicate": 0,
        "peer1_reset_interval": 1,
    },
    "1.2": {
        "file": "001-basic.py",
        "tags": {"basic"},
        "transports": {"tcp", "rdma"},
        "timeout": TEST_TIMEOUT,
        "packet_loss": 0,
        "packet_corrupt": 0,
        "packet_duplicate": 0,
        "peer2_reset_interval": 1,
    },
}
