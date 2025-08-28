#ifndef _LINUX_LFSTACK_H
#define _LINUX_LFSTACK_H

#include <linux/stddef.h>
#include <linux/slab.h>

#ifdef CONFIG_HAVE_CMPXCHG_DOUBLE
#include <linux/types.h>      /* u128 */
#include <asm/cmpxchg.h>      /* try_cmpxchg128 */
#define LFSTACK_LOCKFREE
#else
#include <linux/llist.h>
#endif
#ifdef LFSTACK_LOCKFREE
struct lfstack {
	struct lfstack_el *first;
	uintptr_t seq;
}__aligned(16); /* 128-bit CAS requires 16B alignment of the pair */

union lfstack_header {
	struct lfstack lfs;
	u128 val;
};
#else
#include <linux/llist.h>
struct lfstack {
	struct lfstack_el *first;
	spinlock_t lfs_lock;	/* protect stack pops */
};
#endif

struct lfstack_el {
	struct lfstack_el *next;
};

static inline void lfstack_init(struct lfstack *stack)
{
#ifdef LFSTACK_LOCKFREE
	stack->first = NULL;
	stack->seq = 0;
#else
	init_llist_head((struct llist_head *)&stack->first);
	spin_lock_init(&stack->lfs_lock);
#endif
}

static inline void lfstack_free(struct lfstack *stack)
{
}

static inline void lfstack_push(struct lfstack *stack, struct lfstack_el *el)
{
#ifdef LFSTACK_LOCKFREE
	union lfstack_header prev, new;

	while (true) {
		prev.lfs.first = stack->first;
		prev.lfs.seq = stack->seq;
		new.lfs.first = el;
		new.lfs.seq  = prev.lfs.seq + 1;
		el->next = prev.lfs.first;
		if (try_cmpxchg128((volatile u128 *)&stack->first, &prev.val, new.val))
			break;
	}
#else
	llist_add((struct llist_node *)el, (struct llist_head *)stack);
#endif
}

static inline void lfstack_push_many(struct lfstack *stack, struct lfstack_el *el_first, struct lfstack_el *el_last)
{
#ifdef LFSTACK_LOCKFREE
	union lfstack_header prev, new;

	while (true) {
		prev.lfs.first = stack->first;
		prev.lfs.seq = stack->seq;
		new.lfs.first = el_first;
		new.lfs.seq =  prev.lfs.seq + 1;
		el_last->next  = prev.lfs.first;
		if (try_cmpxchg128((volatile u128 *)&stack->first, &prev.val, new.val))
			break;
	}
#else
	llist_add_batch((struct llist_node *)el_first, (struct llist_node *)el_last, (struct llist_head *)stack);
#endif
}

static inline struct lfstack_el *lfstack_pop(struct lfstack *stack)
{
#ifdef LFSTACK_LOCKFREE
	union lfstack_header prev, new;

	while (true) {
		prev.lfs.first = stack->first;
		if (!prev.lfs.first)
			goto out;
		prev.lfs.seq = stack->seq;
		new.lfs.first = prev.lfs.first->next;
	        new.lfs.seq = prev.lfs.seq + 1;
		if (try_cmpxchg128((volatile u128 *)&stack->first, &prev.val, new.val))
			goto out;
	}
out:
	return prev.lfs.first;
#else
	struct lfstack_el *el;
	unsigned long flags;

	spin_lock_irqsave(&stack->lfs_lock, flags);
	el = (struct lfstack_el *)llist_del_first((struct llist_head *)stack);
	spin_unlock_irqrestore(&stack->lfs_lock, flags);
	return el;
#endif
}

static inline void lfstack_link(struct lfstack_el *first, struct lfstack_el *next)
{
	first->next = next;
}

#endif
