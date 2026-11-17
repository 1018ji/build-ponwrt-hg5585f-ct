#!/usr/bin/env python3
"""Execute real bridge endpoint discovery with mac80211 and DSA paths."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def function(text, name):
    start = text.index('static int '+name+'(')
    brace = text.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def check(source, succeeds):
    text = source.read_text()
    structure = text[text.index('struct nft_forward_info {'):text.index('static int nft_dev_path_info(')]
    functions = function(text, 'nft_dev_path_info')+'\n'+function(text, 'nft_dev_fill_bridge_path')
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t __u16;
typedef uint16_t __be16;
enum { ETH_ALEN=6, NF_FLOW_TABLE_ENCAP_MAX=2, NET_DEVICE_PATH_VLAN_MAX=2 };
enum { ETH_P_PPP_SES=0x8864 };
enum { DEV_PATH_ETHERNET, DEV_PATH_VLAN, DEV_PATH_BRIDGE, DEV_PATH_PPPOE,
       DEV_PATH_DSA, DEV_PATH_MTK_WDMA, DEV_PATH_IEEE80211, DEV_PATH_AIROHA_WDMA };
enum { DEV_PATH_BR_VLAN_UNTAG_HW, DEV_PATH_BR_VLAN_KEEP_HW,
       DEV_PATH_BR_VLAN_TAG, DEV_PATH_BR_VLAN_UNTAG, DEV_PATH_BR_VLAN_KEEP };
enum flow_offload_xmit_type { FLOW_OFFLOAD_XMIT_NEIGH, FLOW_OFFLOAD_XMIT_DIRECT };
enum ip_conntrack_dir { ORIGINAL, REPLY };
struct net_device { int ifindex, mtu; u8 dev_addr[6]; };
struct encap { __u16 id; __be16 proto; };
struct net_device_path {
    int type;
    const struct net_device *dev;
    struct { int vlan_mode; __u16 vlan_id; __be16 vlan_proto; } bridge;
    struct { __u16 id; __be16 proto; u8 h_dest[6]; } encap;
};
struct net_device_path_stack { int num_paths; struct net_device_path path[3]; };
struct net_device_path_ctx {
    bool ieee80211;
    const struct net_device *dev;
    u8 daddr[6];
    int num_vlans;
    struct encap vlan[2];
};
struct nf_flowtable { bool hw; };
struct nft_flowtable { struct nf_flowtable data; };
struct flow_offload_tuple {
    int encap_num, in_vlan_ingress, iifidx, mtu;
    struct encap encap[2];
    struct { int ifidx, hw_ifidx; u8 h_source[6], h_dest[6]; } out;
    enum flow_offload_xmit_type xmit_type;
};
struct flow_offload_tuple_rhash { struct flow_offload_tuple tuple; };
struct flow_offload { struct flow_offload_tuple_rhash tuplehash[2]; };
static struct net_device ap = {.ifindex=12,.mtu=1500},
    lan = {.ifindex=6,.mtu=1500}, eth = {.ifindex=1,.mtu=1500},
    bridge = {.ifindex=10,.mtu=1500};
static int fail_path, observed_vlan_count;
#define BIT(n) (1U << (n))
#define htons(x) __builtin_bswap16(x)
static bool is_zero_ether_addr(const u8 *address)
{ return !memcmp(address, "\0\0\0\0\0\0", 6); }
static void ether_addr_copy(u8 *dst, const u8 *src) { memcpy(dst,src,6); }
static bool nf_flowtable_hw_offload(struct nf_flowtable *table) { return table->hw; }
static bool nft_is_valid_ether_device(const struct net_device *dev) { return !!dev; }
static int dev_fill_bridge_path(struct net_device_path_ctx *ctx,
                                struct net_device_path_stack *stack)
{
    if (fail_path) return -1;
    observed_vlan_count = ctx->num_vlans;
    stack->num_paths=2;
    stack->path[0]=(struct net_device_path){.type=DEV_PATH_BRIDGE,.dev=&bridge,
        .bridge={.vlan_mode=DEV_PATH_BR_VLAN_KEEP}};
    if (ctx->dev == &lan) {
        stack->path[1]=(struct net_device_path){.dev=&ap,
            .type=ctx->ieee80211 ? DEV_PATH_IEEE80211 : DEV_PATH_AIROHA_WDMA};
    } else {
        stack->path[1]=(struct net_device_path){.dev=&lan,.type=DEV_PATH_DSA};
        stack->path[2]=(struct net_device_path){.dev=&eth,.type=DEV_PATH_ETHERNET};
        stack->num_paths=3;
    }
    return 0;
}
'''+structure+functions+r'''
int main(void)
{
    struct flow_offload flow={0};
    struct nft_flowtable ft={.data={.hw=true}};
    u8 src[6]={2,1,2,3,4,5}, dst[6]={2,6,7,8,9,10};
    int err=nft_dev_fill_bridge_path(&flow,&ft,ORIGINAL,&lan,&ap,src,dst);
    assert((err == 0) == EXPECT_SUCCESS);
    if (EXPECT_SUCCESS) {
        assert(flow.tuplehash[REPLY].tuple.iifidx==12);
        assert(flow.tuplehash[ORIGINAL].tuple.out.ifidx==12);
        assert(flow.tuplehash[ORIGINAL].tuple.out.hw_ifidx==12);
        assert(!memcmp(flow.tuplehash[ORIGINAL].tuple.out.h_dest,dst,6));
        assert(flow.tuplehash[ORIGINAL].tuple.mtu==1500);
        assert(flow.tuplehash[ORIGINAL].tuple.xmit_type==FLOW_OFFLOAD_XMIT_DIRECT);
    }
    assert(!nft_dev_fill_bridge_path(&flow,&ft,REPLY,&ap,&lan,dst,src));
    assert(flow.tuplehash[ORIGINAL].tuple.iifidx==6);
    assert(flow.tuplehash[REPLY].tuple.out.ifidx==6);
    assert(flow.tuplehash[REPLY].tuple.out.hw_ifidx==6);
    fail_path=1;
    assert(nft_dev_fill_bridge_path(&flow,&ft,ORIGINAL,&lan,&ap,src,dst)<0);
    fail_path=0;
    assert(nft_dev_fill_bridge_path(&flow,&ft,ORIGINAL,&lan,&eth,src,dst)<0);
    memset(&flow,0,sizeof(flow));
    flow.tuplehash[REPLY].tuple.encap_num=1;
    flow.tuplehash[REPLY].tuple.encap[0]=(struct encap){.id=7,.proto=htons(0x8100)};
    assert(!nft_dev_fill_bridge_path(&flow,&ft,REPLY,&ap,&lan,dst,src));
    assert(observed_vlan_count==1);
    assert(flow.tuplehash[ORIGINAL].tuple.encap_num==1);
    assert(flow.tuplehash[ORIGINAL].tuple.encap[0].id==7);
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as directory:
        path, binary = Path(directory)/'test.c', Path(directory)/'test'
        path.write_text(harness)
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',
                        '-DEXPECT_SUCCESS='+str(int(succeeds)),str(path),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--baseline',type=Path)
    args=parser.parse_args()
    check(args.source, True)
    if args.baseline:
        check(args.baseline, False)
    print('PASS: real Netfilter bridge discovery accepts IEEE80211, preserves DSA/VLAN and rejects invalid paths')


if __name__ == '__main__':
    main()
