RDS self-tests
==============

These scripts provide a coverage test for RDS-TCP and RDS-RDMA (over
RoCE/RXE) by setting up two endpoints and running RDS packets between
them. The TCP path creates two network namespaces; the RDMA path uses
an RXE (soft RoCE) device backed by a veth pair.  A workload of 50000
hashes, each 64 characters in size, is passed over an RDS socket on
this test network with an optional probability of packet loss or
corruption.  A passing test means the RDS stack was able to recover
properly.  The provided config.sh can be used to compile the kernel
with the necessary gcov options; pass -r to also enable the kernel
configs required for the RDMA transport.  The kernel may optionally be
configured to omit the coverage report as well.

USAGE:
	rds_run.sh [-d logdir] [-l packet_loss] [-c packet_corruption]
	           [-u packet_duplicate] [-t timeout]
	           [-T tcp|rdma|tcp,rdma] [-s selector]

OPTIONS:
	-d	Log directory.  If set, logs will be stored in the
		given dir, or skipped if unset.  Log dir can also be
		set through the RDS_LOG_DIR env variable

	-l	Simulates a percentage of packet loss

	-c	Simulates a percentage of packet corruption

	-u	Simulates a percentage of packet duplication.

	-t	Test timeout.  Defaults to tools/testing/selftests/net/rds/settings

	-T	Comma-separated list of transports to test.  Accepts
		"tcp", "rdma", or "tcp,rdma".  Defaults to "tcp".  Use
		config.sh -r to enable required RDMA configs

	-s	Test selector.  A comma-separated list of tokens choosing
		which registered tests to run.  Each token may be a test
		id ("1"), an exact sub-test key ("1.1"), a sub-test range
		("1.*"), an inclusive range ("1-3"), or a tag ("basic").
		"all" runs every registered test.  Defaults to the first
		test.  Tests are skipped if their required transport is
		not selected with -T.

ENV VARIABLES:
	RDS_LOG_DIR	Log directory.  If set, logs will be stored in
			the given dir, or skipped if unset. Log dir
			can also be set with the -d flag.

			Use with --rwdir on the CI path to retain logs after
			test compleation.  Log dir end point must be within
			the specified --rwdir path for logs to persist on
			the host.

	SUDO_USER	The user name that should be used for tcpdump
			--relinquish-privileges.  Set this to a user
			belonging to the sudoers group to avoid drop
			privilege errors with the vng 9p filesystem
			which may result in empty pcaps

EXAMPLE:

    # Create a suitable gcov enabled .config
    tools/testing/selftests/net/rds/config.sh -g

    # Optionally add RDMA configs (CONFIG_RDS_RDMA, CONFIG_RDMA_RXE)
    tools/testing/selftests/net/rds/config.sh -r

    # Alternatly create a gcov disabled .config
    tools/testing/selftests/net/rds/config.sh

    # Config paths may also be specified with the -c flag
    tools/testing/selftests/net/rds/config.sh -c .config.local

    # build the kernel
    vng --build --config .config

    # launch the tests in a VM
    vng -v --rwdir ./ --run . --user root --cpus 4 -- \
        "export PYTHONPATH=tools/testing/selftests/net/; \
         export SUDO_USER=example_user; \
         export RDS_LOG_DIR=tools/testing/selftests/net/rds/rds_logs; \
         tools/testing/selftests/net/rds/rds_run.sh -T tcp,rdma"

