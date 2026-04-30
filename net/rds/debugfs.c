// SPDX-License-Identifier: GPL-2.0
/*
 * Per-connection debugfs interface for RDS.
 *
 * Layout:
 *   /sys/kernel/debug/rds/<netns_inum>/<key>/info   (RO)
 *                                            reset  (WO, drops the conn)
 *
 * <key> is "<laddr>-<faddr>-<trans>-<tos>[-<dev_if>]" so that conns sharing
 * an address pair but differing in trans/tos/dev_if get distinct dirs.
 */
#include <linux/debugfs.h>
#include <linux/inet.h>
#include <linux/seq_file.h>
#include <net/net_namespace.h>
#include <net/netns/generic.h>

#include "rds.h"

static struct dentry *rds_debugfs_root;
static unsigned int rds_debugfs_net_id;

struct rds_debugfs_net {
	struct dentry *ns_dir;
};

/* ---------- per-conn files ---------- */

static int rds_debugfs_info_show(struct seq_file *seq, void *unused)
{
	struct rds_connection *conn = seq->private;

	seq_printf(seq, "laddr     %pI6c\n", &conn->c_laddr);
	seq_printf(seq, "faddr     %pI6c\n", &conn->c_faddr);
	seq_printf(seq, "transport %s\n",    conn->c_trans->t_name);
	seq_printf(seq, "tos       %u\n",    conn->c_tos);
	seq_printf(seq, "dev_if    %d\n",    conn->c_dev_if);
	seq_printf(seq, "loopback  %u\n",    conn->c_loopback);
	/* Path 0 stands in for the connection-level state summary.  For
	 * non-MPATH transports there is only one path; for MPATH this is a
	 * deliberate summary -- the full per-path state is in `paths`.
	 */
	seq_printf(seq, "state     %s\n",
		   rds_conn_path_state_str(&conn->c_path[0]));
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(rds_debugfs_info);

static ssize_t rds_debugfs_reset_write(struct file *file,
				       const char __user *buf,
				       size_t count, loff_t *ppos)
{
	struct rds_connection *conn = file_inode(file)->i_private;
	int i = 0;

	/* Any write triggers a drop; payload is intentionally ignored.
	 * Drop every path rather than calling rds_conn_drop(), which only
	 * drops path 0 and WARNs on multipath-capable transports (e.g. TCP).
	 * Mirrors the per-path iteration in rds_check_all_paths().
	 */
	do {
		rds_conn_path_drop(&conn->c_path[i], false);
	} while (++i < conn->c_npaths);
	return count;
}

static const struct file_operations rds_debugfs_reset_fops = {
	.owner	= THIS_MODULE,
	.open	= simple_open,
	.write	= rds_debugfs_reset_write,
	.llseek	= noop_llseek,
};

/* ---------- per-conn dir lifecycle ---------- */

/* "<laddr>-<faddr>-<trans>-<tos>[-<dev_if>]" */
static void rds_debugfs_keyname(struct rds_connection *conn,
				char *out, size_t outsz)
{
	if (conn->c_dev_if)
		snprintf(out, outsz, "%pI6c-%pI6c-%s-%u-%d",
			 &conn->c_laddr, &conn->c_faddr,
			 conn->c_trans->t_name, conn->c_tos,
			 conn->c_dev_if);
	else
		snprintf(out, outsz, "%pI6c-%pI6c-%s-%u",
			 &conn->c_laddr, &conn->c_faddr,
			 conn->c_trans->t_name, conn->c_tos);
}

void rds_debugfs_add_conn(struct rds_connection *conn)
{
	struct rds_debugfs_net *rnet;
	char name[INET6_ADDRSTRLEN * 2 + 32];

	rnet = net_generic(rds_conn_net(conn), rds_debugfs_net_id);
	/* Skip if this netns has no debugfs dir.  A conn can only be created
	 * in a netns that already exists, and register_pernet_subsys() runs
	 * our net_init for every netns at creation, so the dir is normally
	 * present.  The exception is when debugfs_create_dir() failed in
	 * net_init (ns_dir left NULL); a soft skip is correct there, so this
	 * must not be a WARN_ON.
	 */
	if (!rnet || !rnet->ns_dir)
		return;

	rds_debugfs_keyname(conn, name, sizeof(name));

	conn->c_debugfs = debugfs_create_dir(name, rnet->ns_dir);
	if (IS_ERR_OR_NULL(conn->c_debugfs)) {
		conn->c_debugfs = NULL;
		return;
	}

	debugfs_create_file("info",  0400, conn->c_debugfs, conn,
			    &rds_debugfs_info_fops);
	debugfs_create_file("reset", 0200, conn->c_debugfs, conn,
			    &rds_debugfs_reset_fops);
}

void rds_debugfs_remove_conn(struct rds_connection *conn)
{
	debugfs_remove_recursive(conn->c_debugfs);
	conn->c_debugfs = NULL;
}

/* ---------- per-netns lifecycle ---------- */

static int __net_init rds_debugfs_net_init(struct net *net)
{
	struct rds_debugfs_net *rnet = net_generic(net, rds_debugfs_net_id);
	char name[16];

	snprintf(name, sizeof(name), "%u", net->ns.inum);
	rnet->ns_dir = debugfs_create_dir(name, rds_debugfs_root);
	if (IS_ERR(rnet->ns_dir))
		rnet->ns_dir = NULL;
	return 0;
}

static void __net_exit rds_debugfs_net_exit(struct net *net)
{
	struct rds_debugfs_net *rnet = net_generic(net, rds_debugfs_net_id);

	debugfs_remove_recursive(rnet->ns_dir);
	rnet->ns_dir = NULL;
}

static struct pernet_operations rds_debugfs_net_ops = {
	.init	= rds_debugfs_net_init,
	.exit	= rds_debugfs_net_exit,
	.id	= &rds_debugfs_net_id,
	.size	= sizeof(struct rds_debugfs_net),
};

/* ---------- module init/exit ---------- */

int rds_debugfs_init(void)
{
	int ret;

	rds_debugfs_root = debugfs_create_dir("rds", NULL);
	if (IS_ERR(rds_debugfs_root))
		return PTR_ERR(rds_debugfs_root);

	ret = register_pernet_subsys(&rds_debugfs_net_ops);
	if (ret) {
		debugfs_remove_recursive(rds_debugfs_root);
		rds_debugfs_root = NULL;
	}
	return ret;
}

void rds_debugfs_exit(void)
{
	unregister_pernet_subsys(&rds_debugfs_net_ops);
	debugfs_remove_recursive(rds_debugfs_root);
	rds_debugfs_root = NULL;
}
