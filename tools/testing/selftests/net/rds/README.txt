RDS self-tests
==============

These scripts provide a coverage test for RDS-TCP by creating a vm
with two network namespaces and running rds packets between them.
A loopback network is provisioned with 5% probability of packet
loss or corruption. A workload of 50000 hashes, each 64 characters
in size, are passed over an RDS socket on this test network. A passing
test means the RDS-TCP stack was able to recover properly.

Usage:

    # create a suitable .config
    tools/testing/selftests/net/rds/config.sh

    # build the kernel
    make -j128

    # launch the tests in a VM
    tools/testing/selftests/net/rds/run.sh

An HTML coverage report will be output in /tmp/rds_logs/coverage/.
