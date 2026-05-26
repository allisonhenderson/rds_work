# SPDX-License-Identifier: GPL-2.0

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
