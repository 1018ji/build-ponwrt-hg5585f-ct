#!/usr/bin/env python3
"""Execute WED block binding through simultaneous flowtables and reloads."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    args = parser.parse_args()
    source = (args.kernel/'drivers/net/ethernet/airoha/airoha_wed.c').read_text()
    start = source.index('static int airoha_wed_setup_tc(')
    end = source.index('\nstatic void airoha_wed_ppe_check', start)
    function = source[start:end]
    common = r'''
#include <assert.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
enum tc_setup_type { TC_SETUP_BLOCK, TC_SETUP_FT, TC_OTHER };
enum { FLOW_BLOCK_BIND, FLOW_BLOCK_UNBIND, FLOW_UNKNOWN };
enum { FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS, EGRESS };
struct list_head { int unused; };
struct net_device { int id; };
struct mtk_wed_device { int id; };
struct flow_block_offload {
    int command, binder_type;
    void *block;
    struct list_head *driver_block_list;
};
struct flow_block_cb {
    bool allocated, active;
    void *block, *ident, *priv;
    unsigned int refs;
    struct list_head driver_list;
};
typedef int flow_setup_cb_t(enum tc_setup_type, void *, void *);
static struct flow_block_cb pool[32];
static int allocations, fail_alloc;
#define LIST_HEAD(x) struct list_head x = {0}
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define PTR_ERR(p) ((int)(intptr_t)(p))
static int airoha_wed_setup_cb(enum tc_setup_type t, void *data, void *priv)
{ (void)t; (void)data; (void)priv; return 0; }
static struct flow_block_cb *flow_block_cb_lookup(void *block,
                                               flow_setup_cb_t *cb, void *ident)
{
    (void)cb;
    for (int i = 0; i < 32; i++)
        if (pool[i].active && pool[i].block == block && pool[i].ident == ident)
            return &pool[i];
    return 0;
}
static struct flow_block_cb *flow_block_cb_alloc(flow_setup_cb_t *cb, void *ident,
                                                void *priv, void *release)
{
    (void)cb; (void)release;
    if (fail_alloc) return (void *)(intptr_t)-ENOMEM;
    for (int i = 0; i < 32; i++) if (!pool[i].allocated) {
        pool[i] = (struct flow_block_cb){.allocated=true, .ident=ident, .priv=priv};
        allocations++;
        return &pool[i];
    }
    assert(0); return 0;
}
static void flow_block_cb_incref(struct flow_block_cb *cb) { cb->refs++; }
static int flow_block_cb_decref(struct flow_block_cb *cb)
{ assert(cb->refs); return --cb->refs; }
static void flow_block_cb_add(struct flow_block_cb *cb, struct flow_block_offload *f)
{ cb->active = true; cb->block = f->block; }
static void flow_block_cb_remove(struct flow_block_cb *cb, struct flow_block_offload *f)
{ assert(cb->block == f->block); cb->active = false; cb->allocated = false; }
static void list_add_tail(struct list_head *n, struct list_head *h) { (void)n; (void)h; }
static void list_del(struct list_head *n) { (void)n; }
'''
    test = r'''
int main(void)
{
    int inet, bridge, replacement, unknown;
    struct net_device ap0 = {0}, ap1 = {1};
    struct mtk_wed_device wed = {1};
    struct flow_block_offload f = {.command=FLOW_BLOCK_BIND,
        .binder_type=FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS, .block=&inet};
    assert(!airoha_wed_setup_tc(&wed, &ap0, TC_SETUP_FT, &f));
    assert(allocations == 1);
    assert(flow_block_cb_lookup(&inet, airoha_wed_setup_cb, &ap0)->priv == &wed);
    f.block = &bridge;
    assert(!airoha_wed_setup_tc(&wed, &ap0, TC_SETUP_FT, &f));
    assert(allocations == 2); /* both tables can bind one AP */
    assert(!airoha_wed_setup_tc(&wed, &ap1, TC_SETUP_FT, &f));
    assert(allocations == 3); /* same block, second AP */
    f.block = &inet;
    assert(!airoha_wed_setup_tc(&wed, &ap0, TC_SETUP_FT, &f));
    assert(allocations == 3); /* shared binding increments its reference */
    f.command = FLOW_BLOCK_UNBIND;
    assert(!airoha_wed_setup_tc(&wed, &ap0, TC_SETUP_FT, &f));
    assert(flow_block_cb_lookup(&inet, airoha_wed_setup_cb, &ap0)->refs == 1);
    f.block = &replacement; f.command = FLOW_BLOCK_BIND;
    assert(!airoha_wed_setup_tc(&wed, &ap0, TC_SETUP_FT, &f));
    f.block = &inet; f.command = FLOW_BLOCK_UNBIND;
    assert(!airoha_wed_setup_tc(&wed, &ap0, TC_SETUP_FT, &f));
    assert(!flow_block_cb_lookup(&inet, airoha_wed_setup_cb, &ap0));
    assert(flow_block_cb_lookup(&bridge, airoha_wed_setup_cb, &ap0));
    assert(flow_block_cb_lookup(&replacement, airoha_wed_setup_cb, &ap0));
    f.block = &unknown;
    assert(airoha_wed_setup_tc(&wed, &ap0, TC_SETUP_FT, &f) == -ENOENT);
    f.command = FLOW_BLOCK_BIND; f.binder_type = EGRESS;
    assert(airoha_wed_setup_tc(&wed, &ap0, TC_SETUP_FT, &f) == -EOPNOTSUPP);
    f.binder_type = FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS;
    fail_alloc = 1;
    assert(airoha_wed_setup_tc(&wed, &ap0, TC_SETUP_FT, &f) == -ENOMEM);
    fail_alloc = 0;
    assert(airoha_wed_setup_tc(&wed, &ap0, TC_OTHER, &f) == -EOPNOTSUPP);
    f.command = FLOW_UNKNOWN;
    assert(airoha_wed_setup_tc(&wed, &ap0, TC_SETUP_FT, &f) == -EOPNOTSUPP);
    f.command = FLOW_BLOCK_UNBIND; f.block = &bridge;
    assert(!airoha_wed_setup_tc(&wed, &ap0, TC_SETUP_FT, &f));
    assert(!airoha_wed_setup_tc(&wed, &ap1, TC_SETUP_FT, &f));
    f.block = &replacement;
    assert(!airoha_wed_setup_tc(&wed, &ap0, TC_SETUP_FT, &f));
    for (int i = 0; i < 32; i++) assert(!pool[i].active && !pool[i].allocated);
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as directory:
        source, binary = Path(directory)/'test.c', Path(directory)/'test'
        source.write_text(common+function+test)
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        str(source), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
    print('PASS: real WED setup supports inet+bridge, two APs, overlapping reload, reference cleanup and allocation failure')


if __name__ == '__main__':
    main()
