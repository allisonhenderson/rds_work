#! /usr/bin/env python3

import argparse
import ctypes
import hashlib
import os
import signal
import socket
import subprocess
import sys

libc = ctypes.cdll.LoadLibrary('libc.so.6')
setns = libc.setns

# Helper function for creating a socket inside a network namespace.
# We need this because otherwise RDS will detect that the two TCP
# sockets are on the same interface and use the loop transport instead
# of the TCP transport.
def netns_socket(netns, *args):
    u0, u1 = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)

    child = os.fork()
    if child == 0:
        # change network namespace
        with open(f'/var/run/netns/{netns}') as f:
            ret = setns(f.fileno(), 0)
            # TODO: check ret

        # create socket in target namespace
        s = socket.socket(*args)

        # send resulting socket to parent
        socket.send_fds(u0, [], [s.fileno()])

        #os._exit(0)
        sys.exit(0)

    # receive socket from child
    _, s, _, _ = socket.recv_fds(u1, 0, 1)
    os.waitpid(child, 0)
    u0.close()
    u1.close()
    return socket.fromfd(s[0], *args)

parser = argparse.ArgumentParser()
parser.add_argument('--timeout', type=int, default=0)

args = parser.parse_args()

net0 = 'net0'
net1 = 'net1'

veth0 = 'veth0'
veth1 = 'veth1'

subprocess.check_call(['/usr/sbin/ip', 'netns', 'add', net0])
subprocess.check_call(['/usr/sbin/ip', 'netns', 'add', net1])

subprocess.check_call(['/usr/sbin/ip', 'link', 'add', 'type', 'veth'])

addrs = [
    ('10.0.0.1', 10000),
    ('10.0.0.2', 20000),
]

# move interfaces to separate namespaces so they can no longer be
# bound directly; this prevents rds from switching over from the tcp
# transport to the loop transport.
subprocess.check_call(['/usr/sbin/ip', 'link', 'set', veth0, 'netns', net0, 'up'])
subprocess.check_call(['/usr/sbin/ip', 'link', 'set', veth1, 'netns', net1, 'up'])

# add addresses
subprocess.check_call(['/usr/sbin/ip', '-n', net0, 'addr', 'add', addrs[0][0] + '/32', 'dev', veth0])
subprocess.check_call(['/usr/sbin/ip', '-n', net1, 'addr', 'add', addrs[1][0] + '/32', 'dev', veth1])

# add routes
subprocess.check_call(['/usr/sbin/ip', '-n', net0, 'route', 'add', addrs[1][0] + '/32', 'dev', veth0])
subprocess.check_call(['/usr/sbin/ip', '-n', net1, 'route', 'add', addrs[0][0] + '/32', 'dev', veth1])

# sanity check that our two interfaces/addresses are correctly set up
# and communicating by doing a single ping
subprocess.check_call(['/usr/sbin/ip', 'netns', 'exec', net0, 'ping', '-c', '1', addrs[1][0]])

# simulate packet loss, reordering, corruption, etc.

if True:
    #subprocess.check_call(['/usr/sbin/ip', 'netns', 'exec', net0,
    #    '/usr/sbin/tc', 'qdisc', 'add', 'dev', veth0, 'root', 'netem', 'loss', '10%'])

    for net, iface in [(net0, veth0), (net1, veth1)]:
        subprocess.check_call(['/usr/sbin/ip', 'netns', 'exec', net,
            '/usr/sbin/tc', 'qdisc', 'add', 'dev', iface, 'root', 'netem',
            'reorder', '50%',
            'gap', '5',
            'delay', '10ms',
            'corrupt', '25%',
        ])

# add a timeout
if args.timeout > 0:
    signal.alarm(args.timeout)

sockets = [
    netns_socket(net0, socket.AF_RDS, socket.SOCK_SEQPACKET),
    netns_socket(net1, socket.AF_RDS, socket.SOCK_SEQPACKET),
]

sockets[0].bind(addrs[0])
sockets[1].bind(addrs[1])

#sockets[0].setblocking(0)

send_hash = hashlib.sha256()
recv_hash = hashlib.sha256()

for i in range(500):
    send_data = f'packet {i}'.encode('utf-8')

    #sockets[0].sendto(send_data, socket.MSG_DONTWAIT, addrs[1])
    sockets[0].sendto(send_data, addrs[1])
    send_hash.update(f'packet {i}: {send_data}'.encode('utf-8'))

for i in range(500):
    recv_data = sockets[1].recv(1024)
    recv_hash.update(f'packet {i}: {recv_data}'.encode('utf-8'))

if send_hash.hexdigest() == recv_hash.hexdigest():
    print("Success")
else:
    print("Send/recv hash mismatch")
    sys.exit(1)
