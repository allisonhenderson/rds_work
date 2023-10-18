#! /usr/bin/env python3

import argparse
import ctypes
import hashlib
import os
import select
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

def ip(*args):
    subprocess.check_call(['/usr/sbin/ip'] + list(args))

ip('netns', 'add', net0)
ip('netns', 'add', net1)
ip('link', 'add', 'type', 'veth')

addrs = [
    # we technically don't need different port numbers, but let's do it
    # in case it makes debugging problems easier somehow.
    ('10.0.0.1', 10000),
    ('10.0.0.2', 20000),
]

# move interfaces to separate namespaces so they can no longer be
# bound directly; this prevents rds from switching over from the tcp
# transport to the loop transport.
ip('link', 'set', veth0, 'netns', net0, 'up')
ip('link', 'set', veth1, 'netns', net1, 'up')

# add addresses
ip('-n', net0, 'addr', 'add', addrs[0][0] + '/32', 'dev', veth0)
ip('-n', net1, 'addr', 'add', addrs[1][0] + '/32', 'dev', veth1)

# add routes
ip('-n', net0, 'route', 'add', addrs[1][0] + '/32', 'dev', veth0)
ip('-n', net1, 'route', 'add', addrs[0][0] + '/32', 'dev', veth1)

# sanity check that our two interfaces/addresses are correctly set up
# and communicating by doing a single ping
ip('netns', 'exec', net0, 'ping', '-c', '1', addrs[1][0])

# simulate packet loss, reordering, corruption, etc.

if True:
    #subprocess.check_call(['/usr/sbin/ip', 'netns', 'exec', net0,
    #    '/usr/sbin/tc', 'qdisc', 'add', 'dev', veth0, 'root', 'netem', 'loss', '10%'])

    for net, iface in [(net0, veth0), (net1, veth1)]:
        ip('netns', 'exec', net,
            '/usr/sbin/tc', 'qdisc', 'add', 'dev', iface, 'root', 'netem',
            'reorder', '50%',
            'gap', '5',
            'delay', '10ms',
            'corrupt', '25%',
        )

# add a timeout
if args.timeout > 0:
    signal.alarm(args.timeout)

sockets = [
    netns_socket(net0, socket.AF_RDS, socket.SOCK_SEQPACKET),
    netns_socket(net1, socket.AF_RDS, socket.SOCK_SEQPACKET),
]

for socket, addr in zip(sockets, addrs):
    socket.bind(addr)
    socket.setblocking(0)

fileno_to_socket = {
    socket.fileno(): socket for socket in sockets
}

addr_to_socket = {
    addr: socket for addr, socket in zip(addrs, sockets)
}

socket_to_addr = {
    socket: addr for addr, socket in zip(addrs, sockets)
}

send_hashes = {}
recv_hashes = {}

ep = select.epoll()

for socket in sockets:
    ep.register(socket, select.EPOLLRDNORM)

# Send phase

nr_send = 0
for i in range(500):
    send_data = hashlib.sha256(f'packet {i}'.encode('utf-8')).hexdigest().encode('utf-8')

    # pseudo-random send/receive pattern
    sender = sockets[i % 2]
    receiver = sockets[1 - (i % 3) % 2]

    sender.sendto(send_data, socket_to_addr[receiver])
    send_hashes.setdefault((sender.fileno(), receiver.fileno()), hashlib.sha256()).update(f'<{send_data}>'.encode('utf-8'))
    nr_send = nr_send + 1

# Receive phase

nr_recv = 0
while nr_recv < nr_send:
    for fileno, eventmask in ep.poll():
        if eventmask & select.EPOLLRDNORM:
            try:
                receiver = fileno_to_socket[fileno]
                recv_data, address = receiver.recvfrom(1024)
                sender = addr_to_socket[address]
                recv_hashes.setdefault((sender.fileno(), receiver.fileno()), hashlib.sha256()).update(f'<{recv_data}>'.encode('utf-8'))
                nr_recv = nr_recv + 1
            except BlockingIOError as e:
                pass

# We're done sending and receiving stuff, now let's check if what
# we received is what we sent.

for (sender, receiver), send_hash in send_hashes.items():
    recv_hash = recv_hashes.get((sender, receiver))

    if recv_hash is None or send_hash.hexdigest() != recv_hash.hexdigest():
        print("Send/recv mismatch")
        sys.exit(1)

    print(f"{sender}/{receiver}: ok")

print("Success")
