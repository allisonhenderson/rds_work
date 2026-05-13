#! /usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import ctypes
import errno
import hashlib
import os
import select
import socket
import sys
from rds_common import *

# Allow utils module to be imported from different directory
this_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(this_dir, "../"))
# pylint: disable-next=wrong-import-position,import-error,no-name-in-module
from lib.py.utils import ip # noqa: E402
# pylint: disable-next=wrong-import-position,import-error,no-name-in-module
from lib.py.ksft import ksft_pr # noqa: E402

libc = ctypes.cdll.LoadLibrary('libc.so.6')
setns = libc.setns

# Helper function for creating a socket inside a network namespace.
# We need this because otherwise RDS will detect that the two TCP
# sockets are on the same interface and use the loop transport instead
# of the TCP transport.
def netns_socket(netns, *sock_args):
    """
    Creates sockets inside of network namespace

    :param netns: the name of the network namespace
    :param sock_args: socket family and type
    """
    u0, u1 = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)

    child = os.fork()
    if child == 0:
        try:
            # change network namespace
            with open(f'/var/run/netns/{netns}', encoding='utf-8') as f:
                setns(f.fileno(), 0)
            # create socket in target namespace
            sock = socket.socket(*sock_args)

            # send resulting socket to parent
            socket.send_fds(u0, [], [sock.fileno()])

            os._exit(0)
        except BaseException:
            os._exit(1)

    # receive socket from child
    _, fds, _, _ = socket.recv_fds(u1, 0, 1)
    _, status = os.waitpid(child, 0)
    u0.close()
    u1.close()
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
        raise RuntimeError(
            f"netns_socket child failed in netns {netns} (status={status})")
    return socket.fromfd(fds[0], *sock_args)

def send_burst(socks, ip_addrs, snd_hashes, nr_sent, nr_total):
    """Send until blocked or nr_sent reached. Return updated nr_sent."""
    while nr_sent < nr_total:
        data = hashlib.sha256(
            f'packet {nr_sent}'.encode('utf-8')).hexdigest().encode('utf-8')
        # pseudo-random send/receive pattern
        snd_idx = nr_sent % 2
        rcv_idx = 1 - (nr_sent % 3) % 2

        snd = socks[snd_idx]
        rcv = socks[rcv_idx]
        try:
            snd.sendto(data, ip_addrs[socks.index(rcv)])
        except BlockingIOError:
            return nr_sent
        except OSError as e:
            if e.errno in (errno.ENOBUFS, errno.ECONNRESET, errno.EPIPE):
                return nr_sent
            raise
        snd_hashes.setdefault((snd.fileno(), rcv.fileno()),
                hashlib.sha256()).update(f'<{data}>'.encode('utf-8'))
        nr_sent += 1
    return nr_sent

def recv_burst(epoll, socks, ip_addrs, rcv_hashes, nr_rcv):
    """Drain whatever's readable from epoll. Return updated nr_recv."""
    for filen, evntmask in epoll.poll():
        if not evntmask & select.EPOLLRDNORM:
            continue
        rcv = next(s for s in socks if s.fileno() == filen)
        while True:
            try:
                data, adr = rcv.recvfrom(1024)
            except BlockingIOError:
                break
            snd_idx = ip_addrs.index(adr)
            snd = socks[snd_idx]
            rcv_hashes.setdefault((snd.fileno(), rcv.fileno()),
                    hashlib.sha256()).update(f'<{data}>'.encode('utf-8'))
            nr_rcv += 1
    return nr_rcv

def check_info(socks):
    """
    Check all rds info pages for errors

    :param socks: list of sockets to check
    """

    # the Python socket module doesn't know these
    rds_info_first = 10000
    rds_info_last = 10017

    nr_success = 0
    nr_error = 0

    for sock in socks:
        for optname in range(rds_info_first, rds_info_last + 1):
            # Sigh, the Python socket module doesn't allow us to pass
            # buffer lengths greater than 1024 for some reason. RDS
            # wants multiple pages.
            try:
                sock.getsockopt(socket.SOL_RDS, optname, 1024)
                nr_success = nr_success + 1
            except OSError as e:
                nr_error = nr_error + 1
                if e.errno == errno.ENOSPC:
                    # ignore
                    pass

    ksft_pr(f"getsockopt(): {nr_success}/{nr_error}")

def verify_hashes(snd_hashes, rcv_hashes):
    """Compare send/recv hashes per (sender, receiver) pair."""
    for key, snd_hash in snd_hashes.items():
        rcv_hash = rcv_hashes.get(key)
        if rcv_hash is None:
            ksft_pr("FAIL: No data received")
            return 1
        if snd_hash.hexdigest() != rcv_hash.hexdigest():
            ksft_pr("FAIL: Send/recv mismatch")
            ksft_pr("hash expected:", snd_hash.hexdigest())
            ksft_pr("hash received:", rcv_hash.hexdigest())
            return 1
        ksft_pr(f"{key[0]}/{key[1]}: ok")
    return 0

def run_test(env):
    """Run basic RDS selftest.

    env is a dictionary provided by test.py and is expected to contain:
      - 'addrs':   list of (ip, port) tuples matching the sockets
      - 'netns':   list of network namespace names, or None for RDMA
      - 'flags':   op flags (e.g. OP_FLAG_RDMA)
    """
    addrs = env['addrs']
    netns_list = env['netns']
    flags = env.get('flags', 0)

    if (flags & OP_FLAG_TCP) and (flags & OP_FLAG_RDMA):
        raise RuntimeError(f"Invalid transport flag sets multiple transports: {flags}")

    if flags & OP_FLAG_TCP:
        sockets = [
            netns_socket(netns_list[0], socket.AF_RDS, socket.SOCK_SEQPACKET),
            netns_socket(netns_list[1], socket.AF_RDS, socket.SOCK_SEQPACKET),
        ]

        # Pin the sockets to the TCP transport so it doesn't fail over to a
        # different transport during this test
        for s in sockets:
            s.setsockopt(SOL_RDS, SO_RDS_TRANSPORT, RDS_TRANS_TCP)
    elif flags & OP_FLAG_RDMA:
        sockets = [
            socket.socket(socket.AF_RDS, socket.SOCK_SEQPACKET),
            socket.socket(socket.AF_RDS, socket.SOCK_SEQPACKET),
        ]

        # Pin the sockets to the RDMA transport so it doesn't fail over to a
        # different transport during this test
        for s in sockets:
            s.setsockopt(SOL_RDS, SO_RDS_TRANSPORT, RDS_TRANS_IB)
    else:
        raise RuntimeError(f"Invalid transport flag sets no transports: {flags}")

    for s, addr in zip(sockets, addrs):
        s.bind(addr)
        s.setblocking(0)

    send_hashes = {}
    recv_hashes = {}

    ep = select.epoll()

    for s in sockets:
        ep.register(s, select.EPOLLRDNORM)

    num_packets = env.get('num_packets', DEFAULT_NUM_PKTS)
    nr_send = 0
    nr_recv = 0

    start_reset_procs(env, addrs, netns_list)

    while nr_send < num_packets:

        # Send as much as we can without blocking
        ksft_pr("sending...", nr_send, nr_recv)
        nr_send = send_burst(sockets, addrs, send_hashes, nr_send, num_packets)

        # Receive as much as we can without blocking
        ksft_pr("receiving...", nr_send, nr_recv)
        while nr_recv < nr_send:
            nr_recv = recv_burst(ep, sockets, addrs, recv_hashes, nr_recv)

        # exercise net/rds/tcp.c:rds_tcp_sysctl_reset()
        if netns_list:
            for net in netns_list:
                ip(f"netns exec {net} /usr/sbin/sysctl net.rds.tcp.rds_tcp_rcvbuf=10000")
                ip(f"netns exec {net} /usr/sbin/sysctl net.rds.tcp.rds_tcp_sndbuf=10000")

    stop_reset_procs()
    ksft_pr("done", nr_send, nr_recv)

    check_info(sockets)

    # We're done sending and receiving stuff, now let's check if what
    # we received is what we sent.
    rc = verify_hashes(send_hashes, recv_hashes)

    ep.close()
    for s in sockets:
         s.close()

    return rc
