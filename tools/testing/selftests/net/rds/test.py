#! /usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
This module provides functional testing for the net/rds component.
"""

import argparse
import atexit
import os
import re
import importlib.util
import signal
import subprocess
import sys
import time
from rds_common import *

# Allow utils module to be imported from different directory
this_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(this_dir, "../"))
# pylint: disable-next=wrong-import-position,import-error,no-name-in-module
from lib.py.utils import ip, cmd # noqa: E402
# pylint: disable-next=wrong-import-position,import-error,no-name-in-module
from lib.py.ksft import ksft_pr # noqa: E402

tests_dir = "tests"

NET0 = 'net0'
NET1 = 'net1'

VETH0 = 'veth0'
VETH1 = 'veth1'

tcpdump_procs = []
tcp_addrs = [
    # we technically don't need different port numbers, but this will
    # help identify traffic in the network analyzer
    ('10.0.0.1', 10000),
    ('10.0.0.2', 20000),
]

# RDMA network configs
RXE_DEV0 = 'rxe0'
RXE_DEV1 = 'rxe1'

VETH_RDMA0 = 'veth_rdma0'
VETH_RDMA1 = 'veth_rdma1'

rdma_addrs = [
    ('10.0.0.3', 30000),
    ('10.0.0.4', 30000),
]

signal_handler_label = ""

tap_idx = 0
nr_pass = 0
nr_fail = 0
nr_skip = 0

def stop_pcaps():
    """Stop tcpdump processes.

    We use pop() here to drain the list in the event that the test
    completes after the signal handler is fired.  List will be empty
    if logdir is not set
    """

    if not tcpdump_procs:
        return

    ksft_pr("Stopping network packet captures")
    while tcpdump_procs:
        proc = tcpdump_procs.pop()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

def signal_handler(_sig, _frame):
    """
    Test timed out signal handler
    """
    ksft_pr(f"Test timed out: {signal_handler_label}")
    print(f"not ok {tap_idx} rds selftest {signal_handler_label}")
    sys.exit(1)

def setup_tcp():
    """
    Configure tcp network
    """

    # clean up any leftovers from a previously interrupted run
    teardown_tcp()

    ip(f"netns add {NET0}")
    ip(f"netns add {NET1}")
    ip("link add type veth")

    # Move TCP interfaces into separate namespaces so they can no longer be
    # bound directly; this prevents rds from switching over from the tcp
    # transport to the loop transport.
    ip(f"link set {VETH0} netns {NET0} up")
    ip(f"link set {VETH1} netns {NET1} up")

    # add addresses
    ip(f"-n {NET0} addr add {tcp_addrs[0][0]}/32 dev {VETH0}")
    ip(f"-n {NET1} addr add {tcp_addrs[1][0]}/32 dev {VETH1}")

    # add routes
    ip(f"-n {NET0} route add {tcp_addrs[1][0]}/32 dev {VETH0}")
    ip(f"-n {NET1} route add {tcp_addrs[0][0]}/32 dev {VETH1}")

    # sanity check that our two interfaces/addresses are correctly set up
    # and communicating by doing a single ping
    ip(f"netns exec {NET0} ping -c 1 {tcp_addrs[1][0]}")

    # Start a packet capture on each network
    if logdir is not None:
        for netn in [NET0, NET1]:
            pcap = logdir+'/rds-'+netn+'.pcap'

            tcpdump_cmd = ['ip', 'netns', 'exec', netn, '/usr/sbin/tcpdump']
            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user:
                tcpdump_cmd.extend(['-Z', sudo_user])
            tcpdump_cmd.extend(['-i', 'any', '-w', pcap])

            # pylint: disable-next=consider-using-with
            p = subprocess.Popen(tcpdump_cmd,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            tcpdump_procs.append(p)

    # Install a netem qdisc with zero fault injection so set_test_params()
    # can use 'tc qdisc change' to update it per-test.
    for netn, iface in [(NET0, VETH0), (NET1, VETH1)]:
        ip(f"netns exec {netn} /usr/sbin/tc qdisc add dev {iface} root netem")

def teardown_tcp():
    """
    Tear down the tcp network configured by setup_tcp().

    Removing the namespaces also removes the veth pair, addresses,
    routes, and netem qdisc that live inside them.  fail=False so
    this is safe to call in error paths after a partial or complete setup.
    """
    cmd(f"ip netns del {NET0}", fail=False)
    cmd(f"ip netns del {NET1}", fail=False)

def get_iface_mac(iface):
    """Return the MAC address of a local network interface."""
    out = subprocess.check_output(['ip', 'link', 'show', iface], text=True)
    mac = re.search(r'link/ether\s+([0-9a-f:]+)', out)
    if not mac:
        raise RuntimeError(f"Cannot determine MAC address of {iface}")
    return mac.group(1)

def setup_rdma():
    """
    Configure rdma network
    """

    # remove links left over by previously interrupted run.
    teardown_rdma()

    # use call here since modprobe may fail if the rdma_rxe
    # module is built-in
    subprocess.call(['modprobe', 'rdma_rxe'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ip(f"link add {VETH_RDMA0} type veth peer name {VETH_RDMA1}")

    ip(f"link set {VETH_RDMA0} up")
    ip(f"link set {VETH_RDMA1} up")

    # Since both addresses are in the same namespace, the source address
    # is always local, so enable accept_local
    cmd(f"/usr/sbin/sysctl -q net.ipv4.conf.{VETH_RDMA0}.accept_local=1")
    cmd(f"/usr/sbin/sysctl -q net.ipv4.conf.{VETH_RDMA1}.accept_local=1")

    # Reverse path filters must be disabled so that the local routes don't
    # cause RPF failures.
    cmd(f"/usr/sbin/sysctl -q net.ipv4.conf.{VETH_RDMA0}.rp_filter=0")
    cmd(f"/usr/sbin/sysctl -q net.ipv4.conf.{VETH_RDMA1}.rp_filter=0")

    # add addresses
    ip(f"addr add {rdma_addrs[0][0]}/32 dev {VETH_RDMA0}")
    ip(f"addr add {rdma_addrs[1][0]}/32 dev {VETH_RDMA1}")

    # add routes
    ip(f"route add {rdma_addrs[1][0]}/32 dev {VETH_RDMA0}")
    ip(f"route add {rdma_addrs[0][0]}/32 dev {VETH_RDMA1}")

    # ARP will not resolve neighbor IPs on /32 routes without a subnet.
    # Avoid this by adding neighbors directly so RDMA CM can populate path
    # records with correct mac addrs without waiting for the ARP.
    mac0 = get_iface_mac(VETH_RDMA0)
    mac1 = get_iface_mac(VETH_RDMA1)
    ip(f"neigh add {rdma_addrs[1][0]} lladdr {mac1} dev {VETH_RDMA0} nud permanent")
    ip(f"neigh add {rdma_addrs[0][0]} lladdr {mac0} dev {VETH_RDMA1} nud permanent")

    cmd(f'rdma link add {RXE_DEV0} type rxe netdev {VETH_RDMA0}')
    cmd(f'rdma link add {RXE_DEV1} type rxe netdev {VETH_RDMA1}')

    time.sleep(1)  # allow RXE devices to initialise

    # Start a packet capture on each network
    if logdir is not None:
        for iface in [VETH_RDMA0, VETH_RDMA1]:
            pcap = logdir+'/rds-roce-'+iface+'.pcap'

            tcpdump_cmd = ['/usr/sbin/tcpdump']
            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user:
                tcpdump_cmd.extend(['-Z', sudo_user])
            tcpdump_cmd.extend(['-i', iface, '-w', pcap])

            # pylint: disable-next=consider-using-with
            p = subprocess.Popen(tcpdump_cmd,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            tcpdump_procs.append(p)

    # Install a netem qdisc with zero fault injection so set_test_params()
    # can use 'tc qdisc change' to update it per-test.
    for iface in [VETH_RDMA0, VETH_RDMA1]:
        cmd(f"/usr/sbin/tc qdisc add dev {iface} root netem")

def increment_ports(addrs, inc):
    """Increment port numbers in the addrs list by inc.
       Use between tests to make the port numbers unique.

    addrs: list of (ip, port) tuples
    inc: int
    """
    return [(addr, port + inc) for addr, port in addrs]

def parse_selector(selector):
    """Parse a -s argument into the set of test keys to run.

    Selector contains a comma-separated list of the following:
      - a major number:     "1"   (matches "1.0")
      - an exact key:       "1.1"
      - a major range:      "1.*" (matches "1.0", "1.1")
      - an inclusive range: "1-3" (matches "1.0","1.1" ... "3.0","3.1")
      - a tag name:         "basic"
    An empty or "all" selector runs every registered test.
    """
    all_keys = list(all_tests.keys())
    if not selector or selector == "all":
        return set(all_keys)

    # a set of tags and the tests they correspond to
    test_tags = {}
    for key, test in all_tests.items():
        for tag in test.get("tags", set()):
            test_tags.setdefault(tag, set()).add(key)

    # keys for the tests we are going to run
    keys = set()
    for token in selector.split(','):
        token = token.strip()
        if not token:
            continue

        # check if token is a digit and convert
        # to float to match the decimal scheme
        if (token.isdigit()):
            token = token+".0"

        # If the token is an exact match,
        # add it to our key collection
        if token in all_tests.keys():
            keys.add(token)

        # select sub-tests range (e.g. "1.*" -> "1.0", "1.1")
        elif (token.endswith(".*") and token[0:-2].isdigit()):
            major_num = token[0:-2]
            test_range = [k for k in all_keys if k.startswith(major_num + '.')]
            keys.update(test_range)

        # inclusive range of major numbers
        # (eg "1-3" -> "1.0", "1.1", "2.0", "2.1")
        elif re.fullmatch(r'\d+-\d+', token):
            lo, hi = (int(x) for x in token.split('-'))
            for maj in range(lo, hi + 1):
                test_range = [k for k in all_keys if k.startswith(str(maj) + '.')]
                keys.update(keys.update)

        # check for tag matches
        elif token in test_tags:
            keys.update(test_tags[token])

        else:
            raise SystemExit(f"test.py: unknown test selector: {token!r}")

    return keys

def set_test_params(key, args, transport):
    """Apply per-test netem parameters from the registry.

    Command-line values (non-None) take priority; otherwise the
    registry entry for key is used.  Uses 'tc qdisc change' so the
    existing netem qdisc installed by setup_tcp/setup_rdma is updated
    in-place rather than re-created.
    """
    test = all_tests.get(key)
    if test is None:
        return

    loss    = args.loss       if args.loss       is not None else test["packet_loss"]
    corrupt = args.corruption if args.corruption is not None else test["packet_corrupt"]
    dup     = args.duplicate  if args.duplicate  is not None else test["packet_duplicate"]

    loss_s    = str(loss)    + '%'
    corrupt_s = str(corrupt) + '%'
    dup_s     = str(dup)     + '%'

    if transport == "rdma":
        for iface in [VETH_RDMA0, VETH_RDMA1]:
            cmd(f"/usr/sbin/tc qdisc change dev {iface} root netem "
                f"corrupt {corrupt_s} loss {loss_s} duplicate {dup_s}")
    else:
        for net, iface in [(NET0, VETH0), (NET1, VETH1)]:
            ip(f"netns exec {net} /usr/sbin/tc qdisc change dev {iface} root netem "
               f"corrupt {corrupt_s} loss {loss_s} duplicate {dup_s}")

def teardown_rdma():
    """
    Tear down the rdma network configured by setup_rdma().
    """

    # remove links left over by previously interrupted run.
    cmd(f'rdma link del {RXE_DEV0}', fail=False)
    cmd(f'rdma link del {RXE_DEV1}', fail=False)
    cmd(f'ip link del {VETH_RDMA0}', fail=False)


#Parse out command line arguments.  We take an optional
# timeout parameter and an optional log output folder
parser = argparse.ArgumentParser(description="init script args",
                  formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("-d", "--logdir", action="store",
                    help="directory to store logs", default=None)
parser.add_argument("-T", "--transport", default="tcp",
                    help="Comma-separated list of transports to test: "
                         "tcp, rdma, or tcp,rdma.  Each matching test "
                         "is run once per transport.  "
                         "'rdma' requires CONFIG_RDS_RDMA and rdma_rxe.")
parser.add_argument("-s", "--tests", default="",
                    help="Comma-separated test selector: numbers (1), "
                         "ranges (1-3), or tags (basic,stress). "
                         "Default: run every discovered test.")
parser.add_argument('-t', '--timeout', help="timeout to terminate hung test",
                    type=int, default=None)
parser.add_argument('-l', '--loss', help="Simulate packet loss (%%)",
                    type=int, default=None)
parser.add_argument('-c', '--corruption', help="Simulate packet corruption (%%)",
                    type=int, default=None)
parser.add_argument('-u', '--duplicate', help="Simulate packet duplication (%%)",
                    type=int, default=None)
args = parser.parse_args()
logdir=args.logdir

# check transport is either tcp or rdma
transports = [t.strip() for t in args.transport.split(',')]
for t in transports:
    if t not in ('tcp', 'rdma'):
        raise SystemExit(f"test.py: unknown transport: {t!r}")

# Register stop_pcaps before any network setups so that any partially setup
# tcpdumps are still cleaned up on error
atexit.register(stop_pcaps)

# Set up all requested transports upfront so network plumbing is
# ready before any test runs.
transport_envs = {}
FLAGS = 0
if 'tcp' in transports:
    # Register cleanups before setups to handle partial setups that error'd out
    atexit.register(teardown_tcp)
    setup_tcp()
    transport_envs['tcp'] = {
        'addrs': tcp_addrs,
        'netns': [NET0, NET1],
        'flags': FLAGS | OP_FLAG_TCP,
    }

if 'rdma' in transports:
    atexit.register(teardown_rdma)
    setup_rdma()
    transport_envs['rdma'] = {
        'addrs': rdma_addrs,
        'netns': None,
        'flags': FLAGS | OP_FLAG_RDMA,
    }

# find all tests to run
test_keys = parse_selector(args.tests)

# kselftest SKIP exit code
SKIP_RC = 4

# Build the runnable list across all key + transport combinations
to_run = []
for transport in transports:
    for key in all_tests:
        if key not in test_keys:
            continue

        test = all_tests[key]
        if transport not in test["transports"]:
            continue

        to_run.append((transport, key))

# Print list of target tests for proper TAP output
print("TAP version 13")
print(f"1..{len(to_run)}")

test_mods = {}
for transport, key in to_run:
    tap_idx += 1
    test = all_tests[key]

    fname = test["file"].removesuffix(".py")
    if fname not in test_mods:
        test_mods[fname] = importlib.import_module(tests_dir+"."+fname)

    mod = test_mods[fname]

    label = f"rds.{key}-{fname}.{transport}"
    set_test_params(key, args, transport)

    # Shift ports between tests so a lingering TIME_WAIT from the
    # previous test cannot collide with the next one.
    # Also helps to distinguish test traffic in network dumps
    transport_envs[transport]['addrs'] = increment_ports(
        transport_envs[transport]['addrs'], 1000)
    env = dict(transport_envs[transport])
    env['num_packets'] = test.get('num_packets', 50000)
    env['peer1_reset_interval'] = test.get('peer1_reset_interval', 0)
    env['peer2_reset_interval'] = test.get('peer2_reset_interval', 0)

    # Per-test timeout
    # If user passed a command-line time out use it
    # Otherwise use the timeout from the test profile if available
    # if none of the above is provided, then no alarm.
    signal_timeout = (args.timeout if args.timeout is not None
                      else test.get("timeout", 0))
    if signal_timeout > 0:
        signal_handler_label = label
        signal.signal(signal.SIGALRM, signal_handler)
        signal.alarm(signal_timeout)

    ksft_pr(f"RUN: {label}")
    try:
        rc = mod.run_test(env)
    except RdsSkipEx as e:
        print(f"ok {tap_idx} {label} # SKIP {e}")
        nr_skip += 1
        continue
    finally:
        signal.alarm(0)
    if rc == SKIP_RC:
        print(f"ok {tap_idx} {label} # SKIP reported by test")
        nr_skip += 1
    elif rc != 0:
        print(f"not ok {tap_idx} {label}")
        nr_fail += 1
    else:
        print(f"ok {tap_idx} {label}")
        nr_pass += 1

ksft_pr(f"Totals: pass:{nr_pass} fail:{nr_fail} skip:{nr_skip}")
if nr_fail > 0:
    sys.exit(1)
elif nr_pass == 0:
    sys.exit(SKIP_RC)
else:
    sys.exit(0)
