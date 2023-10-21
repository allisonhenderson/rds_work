#! /bin/bash

set -e
set -u
#set -x

mount -t proc none /proc
mount -t sysfs none /sys
mount -t tmpfs none /tmp
mount -t tmpfs none /var/run
mount -t debugfs none /sys/kernel/debug

echo running RDS tests...
#time python3 "$(dirname "$0")/test.py" || true
#strace -f -e trace=socketpair,sendmsg,recvmsg /usr/bin/python3 "$(dirname $0)/tools/testing/selftests/net/rds/test.py" || true

rm /home/vegard/rds-log.txt
strace -o "/home/vegard/rds-log.txt" /usr/bin/python3 "$(dirname "$0")/test.py" || true

killall -q tcpdump

echo saving coverage data...
(set +x; cd /sys/kernel/debug/gcov; find -name '*.gcda' | \
while read f
do
	cp /sys/kernel/debug/gcov/$f /$f
done)

/usr/sbin/poweroff --no-wtmp --force
