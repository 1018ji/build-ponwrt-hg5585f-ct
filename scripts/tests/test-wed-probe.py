#!/usr/bin/env python3
"""Compile the shipped PPE/WED probe functions against HG5585F port layouts."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def function(text, name):
    match = re.search(r'^static int ' + re.escape(name) + r'\(', text, re.M)
    assert match, name
    brace = text.index('{', match.start())
    end, depth = brace + 1, 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    driver = args.kernel/'drivers/net/ethernet/airoha'
    header = (driver/'airoha_eth.h').read_text()
    gdm_ids = re.search(r'enum airoha_gdm_index \{[\s\S]*?\};', header).group()
    limits = '\n'.join(re.search(r'^#define ' + name + r'\s+\d+', header, re.M).group()
                       for name in ('AIROHA_MAX_NUM_GDM_PORTS', 'AIROHA_MAX_NUM_GDM_DEVS'))
    prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
#define ARRAY_SIZE(a) ((int)(sizeof(a) / sizeof((a)[0])))
#define __releases(lock)
#define EPROBE_DEFER 517
#define PCI_VENDOR_ID_MEDIATEK 0x14c3
#define MTK_WED_BUS_PCIE 1
#define AIROHA_NPU_WLAN_ABI_HG5585F_2024 1
#define CONFIG_NET_AIROHA_PPE_HG5585F 1
#define IS_ENABLED(v) (v)
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define upper_32_bits(v) ((uint64_t)(v) >> 32)
#define MTK_WDMA_GLO_CFG 0
#define MTK_WDMA_GLO_CFG_TX_DMA_EN 1
#define MTK_WDMA_GLO_CFG_TX_DMA_BUSY 2
#define MTK_WED_RESET_WED 1
#define DL_FLAG_AUTOREMOVE_CONSUMER 2
#define airoha_wlan_dbg(dev, component, fmt, ...) do { \
    if (debug_enabled) dev_err(dev, fmt, ##__VA_ARGS__); \
} while (0)
struct device_node { int value; };
struct device { struct device_node *of_node; };
struct pci_dev { int vendor, device; void *bus; struct device dev; };
struct device_link { unsigned int flags; };
struct airoha_gdm_dev { int nbq; };
'''
    middle = r'''
struct airoha_gdm_port { struct airoha_gdm_dev *devs[AIROHA_MAX_NUM_GDM_DEVS]; int id; };
struct airoha_eth { struct device *dev; struct airoha_gdm_port *ports[AIROHA_MAX_NUM_GDM_PORTS]; };
struct airoha_ppe_dev { void *priv; struct { int (*prepare_legacy)(struct airoha_ppe_dev *); int (*set_wed)(struct airoha_ppe_dev *,bool); } ops; };
struct airoha_ppe { struct airoha_eth *eth; bool wed_enabled; };
struct airoha_npu { unsigned int wlan_abi; struct device *dev; };
struct mtk_wed_device {
    struct { int bus_type; struct pci_dev *pci_dev; int (*offload_enable)(struct mtk_wed_device *); void (*offload_disable)(struct mtk_wed_device *); } wlan;
    void *priv; struct device *dev; int irq,wdma_idx,version; bool airoha;
};
struct airoha_wed { struct device *dev; struct mtk_wed_device *wlan; bool poisoned,legacy,tokens_reserved,ppe_configured; struct airoha_ppe_dev *ppe; u32 *wdma; int irq; const char *last_attach_stage; int last_attach_error; u32 last_wdma_cfg; };
static int flow_offload_mutex,airoha_wed_lock,held,mtu_calls,error_records;
static char last_error[512];
static void mutex_lock(int *lock) { (void)lock;held++; }
static void mutex_unlock(int *lock) { (void)lock;assert(held);held--; }
static void rcu_read_unlock(void) {}
static void airoha_ppe_wed_mtu(struct airoha_ppe *ppe) { (void)ppe;mtu_calls++; }
static void dev_err(struct device *dev,const char *format,...) __attribute__((format(printf,2,3)));
static void dev_err(struct device *dev,const char *format,...)
{ (void)dev;va_list ap;va_start(ap,format);vsnprintf(last_error,sizeof(last_error),format,ap);va_end(ap);error_records++; }
static struct airoha_wed *airoha_wed1;
static struct device_node node;
static struct device supplier={.of_node=&node};
static struct airoha_npu npu={.dev=&supplier};
static int npu_error,ppe_error,prepare_error,quiesce_error,reset_error,buffer_error,token_error,set_wed_error;
static int npu_refs,ppe_refs,node_refs,buffers,token_disables,links,managed_deletes;
static bool link_error,firmware_error,debug_enabled;
static struct device_link link;
static struct device_node *of_parse_phandle(struct device_node *np,const char *name,int index)
{ (void)np;(void)name;(void)index;node_refs++;return &node; }
static void of_node_put(struct device_node *np) { assert(np==&node);node_refs--; }
static int pci_domain_nr(void *bus) { (void)bus;return 1; }
static uint64_t pci_resource_start(struct pci_dev *pci,int index) { (void)pci;(void)index;return 0x24000000; }
static struct airoha_npu *airoha_npu_get(struct device *dev)
{ (void)dev;if(npu_error)return ERR_PTR(npu_error);npu_refs++;return &npu; }
static void airoha_npu_put(struct airoha_npu *p) { assert(p==&npu && npu_refs>0);npu_refs--; }
static int of_property_read_string_array(struct device_node *np,const char *name,const char **names,int count)
{ (void)np;(void)name;assert(count==2);names[0]="airoha/en7581_npu_rv32.bin";names[1]="airoha/en7581_npu_data.bin";return firmware_error ? -EINVAL : 2; }
static int prepare_legacy(struct airoha_ppe_dev *ppe) { (void)ppe;return prepare_error; }
static int set_wed(struct airoha_ppe_dev *ppe,bool enable) { (void)ppe;assert(enable);return set_wed_error; }
static struct airoha_ppe_dev ppe_supplier={.ops={.prepare_legacy=prepare_legacy,.set_wed=set_wed}};
static struct airoha_ppe_dev *airoha_ppe_get_dev(struct device *dev)
{ (void)dev;if(ppe_error)return ERR_PTR(ppe_error);ppe_refs++;return &ppe_supplier; }
static void airoha_ppe_put_dev(struct airoha_ppe_dev *ppe) { assert(ppe==&ppe_supplier && ppe_refs>0);ppe_refs--; }
static struct device_link *device_link_add(struct device *consumer,struct device *provider,unsigned int flags)
{ (void)consumer;assert(provider==&supplier && flags==DL_FLAG_AUTOREMOVE_CONSUMER);if(link_error)return NULL;link.flags=flags;links++;return &link; }
static void __attribute__((unused)) device_link_del(struct device_link *p)
{ assert(p==&link);if(p->flags==DL_FLAG_AUTOREMOVE_CONSUMER)managed_deletes++;else links--; }
static u32 readl(u32 *address) { return *address; }
static int airoha_wed_quiesce(struct mtk_wed_device *wed) { (void)wed;return quiesce_error; }
static int airoha_wed_reset(struct mtk_wed_device *wed,int mask) { (void)wed;(void)mask;return reset_error; }
static int airoha_wed_buffers_alloc(struct mtk_wed_device *wed) { (void)wed;buffers++;return buffer_error; }
static void airoha_wed_buffers_free(struct mtk_wed_device *wed) { (void)wed;buffers=0; }
static int offload_enable(struct mtk_wed_device *wed) { (void)wed;return token_error; }
static void offload_disable(struct mtk_wed_device *wed) { (void)wed;token_disables++; }
'''
    tests = r'''
static void port_tests(void)
{
    struct airoha_eth eth={0};struct airoha_ppe ppe={.eth=&eth};struct airoha_ppe_dev dev={.priv=&ppe};
    struct airoha_gdm_port ports[AIROHA_MAX_NUM_GDM_PORTS]={0};struct airoha_gdm_dev netdev={.nbq=0};
    /* HG5585F: Ethernet GDM1, PON GDM2, no GDM3 netdev, LAN1 on GDM4. */
    for(int i=0;i<AIROHA_MAX_NUM_GDM_PORTS;i++){ports[i].id=i+1;eth.ports[i]=&ports[i];if(i!=AIROHA_GDM3_IDX-1)ports[i].devs[0]=&netdev;}
    eth.ports[AIROHA_GDM3_IDX-1]=NULL;
    assert(airoha_ppe_set_wed(&dev,true)==0 && ppe.wed_enabled && mtu_calls==1 && held==0);
    eth.ports[AIROHA_GDM3_IDX-1]=&ports[AIROHA_GDM3_IDX-1];
    assert(airoha_ppe_set_wed(&dev,true)==0 && mtu_calls==2 && held==0);
    for(int i=0;i<AIROHA_MAX_NUM_GDM_DEVS;i++){
        int before=mtu_calls;ppe.wed_enabled=false;ports[AIROHA_GDM3_IDX-1].devs[i]=&netdev;netdev.nbq=i+4;
        assert(airoha_ppe_set_wed(&dev,true)==-EBUSY && !ppe.wed_enabled && mtu_calls==before && held==0);
        assert(airoha_ppe_set_wed(&dev,false)==0 && !ppe.wed_enabled && mtu_calls==before+1 && held==0);
        ports[AIROHA_GDM3_IDX-1].devs[i]=NULL;
    }
    puts("PASS: HG5585F LAN1/GDM4 and PON coexist with WED; every occupied GDM3 slot still rejects enable");
}
static void attach_tests(void)
{
    const char *stages[]={"provider","ownership","npu_supplier","firmware_abi","ppe_supplier","ppe_prepare","device_link","wdma_busy","quiesce","reset","buffers","tokens","ppe_enable","ppe_enable"};
    for(int scenario=0;scenario<14;scenario++){
        struct pci_dev pci={.vendor=PCI_VENDOR_ID_MEDIATEK,.device=0x7906};u32 wdma=0;
        struct airoha_wed aw={.dev=&supplier,.irq=42,.wdma=&wdma};
        struct mtk_wed_device wed={.wlan={.bus_type=MTK_WED_BUS_PCIE,.pci_dev=&pci,.offload_enable=offload_enable,.offload_disable=offload_disable}};
        npu_error=ppe_error=prepare_error=quiesce_error=reset_error=buffer_error=token_error=set_wed_error=0;
        npu_refs=ppe_refs=node_refs=buffers=token_disables=links=managed_deletes=error_records=0;
        link_error=firmware_error=false;npu.wlan_abi=0;airoha_wed1=&aw;
        switch(scenario){case 0:airoha_wed1=NULL;break;case 1:aw.poisoned=true;break;case 2:npu_error=-EPROBE_DEFER;break;case 3:firmware_error=true;break;case 4:ppe_error=-EPROBE_DEFER;break;case 5:npu.wlan_abi=AIROHA_NPU_WLAN_ABI_HG5585F_2024;prepare_error=-EBUSY;break;case 6:link_error=true;break;case 7:wdma=MTK_WDMA_GLO_CFG_TX_DMA_EN;break;case 8:quiesce_error=-ETIMEDOUT;break;case 9:reset_error=-ETIMEDOUT;break;case 10:buffer_error=-ENOMEM;break;case 11:token_error=1;break;case 12:set_wed_error=-EBUSY;break;default:break;}
        int result=airoha_wed_attach(&wed);
        assert(held==0 && npu_refs==0 && node_refs==0 && managed_deletes==0);
        if(scenario<13){
            assert(result<0 && error_records==1 && strstr(last_error,stages[scenario]));
            assert(aw.wlan==NULL && !aw.ppe_configured && !aw.tokens_reserved && buffers==0 && ppe_refs==0);
            assert(!wed.priv && !wed.airoha);
            if(scenario!=0){assert(aw.last_attach_error==result && !strcmp(aw.last_attach_stage,stages[scenario]));}
            /* Driver core performs AUTOREMOVE_CONSUMER cleanup on failed probe. */
            links=0;
        }else{
            assert(result==0 && aw.wlan==&wed && aw.ppe_configured && aw.tokens_reserved && wed.airoha);
            assert(ppe_refs==1 && buffers==1 && links==1 && aw.last_attach_error==0);
            ppe_refs=buffers=links=0;
        }
    }
    puts("PASS: WED attach failures retain no PPE/tokens/buffers, preserve managed link ownership and report stages with debug off");
}
int main(void){port_tests();attach_tests();return 0;}
'''
    code='\n'.join((prefix,gdm_ids,limits,middle,
                    function((driver/'airoha_ppe.c').read_text(),'airoha_ppe_set_wed'),
                    function((driver/'airoha_wed.c').read_text(),'airoha_wed_attach'),tests))
    with tempfile.TemporaryDirectory(prefix='hg5585f-wed-probe-test-') as temporary:
        source,binary=Path(temporary)/'test.c',Path(temporary)/'test'
        source.write_text(code)
        subprocess.run([args.cc,'-std=gnu11','-Wall','-Wextra','-Werror','-Wformat=2',
                        '-fsanitize=address,undefined',str(source),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True)


if __name__=='__main__':
    main()
