#!/usr/bin/env python3
"""Exercise stalled WFDMA recovery, producer ordering and MMIO-free diagnostics."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def function(text, name):
    match = re.search(r'^(?:static )?(?:int|void)\s+' + name + r'\([^;]*?\)\n\{', text, re.M)
    assert match, name
    brace = text.index('{', match.start())
    end, depth = brace + 1, 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end]


def execute(cc, source):
    with tempfile.TemporaryDirectory(prefix='mt7915-dma-recovery-') as directory:
        path, binary = Path(directory)/'test.c', Path(directory)/'test'
        path.write_text(source)
        subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wformat=2', '-Wno-unused-parameter', '-fsanitize=address,undefined', str(path),
                        '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <errno.h>
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wunused-variable"
typedef uint32_t u32;
typedef uint64_t u64;
#define BIT(n) (1U << (n))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define MT_RXQ_NPU0 0
#define MT_RXQ_NPU1 1
#define MT_WFDMA0_GLO_CFG 0x208
#define MT_WFDMA0_RST 0x100
#define MT_WFDMA0_PCIE1(n) (0x1000+(n))
#define MT_WFDMA0(n) (n)
#define MT_WFDMA0_GLO_CFG_TX_DMA_EN BIT(0)
#define MT_WFDMA0_GLO_CFG_RX_DMA_EN BIT(2)
#define MT_WFDMA0_RST_DMASHDL_ALL_RST BIT(5)
#define MT_WFDMA0_RST_LOGIC_RST BIT(4)
#define MT76_RESET 0
#define MT76_MCU_RESET 1
#define MT_BAND1 1
#define MT_INT1_MASK_CSR 0x308
#define MT_INT1_SOURCE_CSR 0x30c
#define MT_PCIE_MAC_INT_ENABLE 0x310
#define MT_PCIE1_MAC_INT_ENABLE 0x314
#define MT_PCIE1_MAC_INT_ENABLE_MT7916 0x318
struct mt76_queue { bool stopped; unsigned int head,tail,ndesc; };
struct pci_dev { bool master; };
struct napi_struct { bool enabled; };
struct worker { bool enabled; };
struct airoha_npu { int unused; };
struct mtk_wed_device { bool active,dma_quarantined; };
struct mt76_phy { unsigned long state; };
struct mt7915_phy { int unused; };
struct mt7915_hif { struct pci_dev *dev; };
struct mt76_dev {
    struct pci_dev *dev;
    struct { bool npu_legacy_rx,npu_quarantined; struct airoha_npu *npu; void *ppe_dev;
             struct mtk_wed_device wed; u32 npu_rx_last_info,npu_rx_last_vendor;
             u64 npu_rx_host_packets,npu_rx_host_errors; } mmio;
    struct mt76_queue q_rx[2];
    struct mt76_phy *phys[2];
    struct worker tx_worker;
    struct napi_struct napi[2],tx_napi;
    int mcu;
};
struct mt7915_dev {
    struct mt76_dev mt76;
    struct mt76_phy mphy;
    struct mt7915_hif *hif2;
    bool npu_legacy_failed,npu_legacy_configured,npu_ppe_forwarding;
    struct {bool hw_full_reset,wed_failed;} recovery;
};
static u32 regs[0x2000/4];
static int provider_error,provider_calls,poisons,polls,resets,barrier_step,diagnostics;
static bool stuck_after_reset,no_provider;
static char logbuf[4096];
static void __attribute__((format(printf,2,3))) log_message(void *dev,const char *fmt,...)
{ (void)dev;va_list args;va_start(args,fmt);vsnprintf(logbuf,sizeof(logbuf),fmt,args);va_end(args); }
#define dev_err log_message
#define dev_warn log_message
#define dev_info log_message
#define rcu_dereference_protected(p,c) (p)
#define rcu_access_pointer(p) (p)
#define to_pci_dev(p) (p)
#define mt76_for_each_q_rx(d,i) for((i)=0;(i)<2;(i)++)
static void mt7915_irq_disable(struct mt7915_dev *d,u32 mask) { (void)d;assert(mask==~0U); }
static void mt76_npu_disable_irqs(struct mt76_dev *d) { (void)d; }
static void mt76_clear(struct mt7915_dev *d,u32 reg,u32 mask)
{ (void)d;assert(reg<sizeof(regs));regs[reg/4]&=~mask; }
static void mt76_set(struct mt7915_dev *d,u32 reg,u32 mask)
{
    (void)d;assert(reg<sizeof(regs));regs[reg/4]|=mask;
    if(reg==MT_WFDMA0_RST||reg==MT_WFDMA0_RST+0x1000) {
        assert(provider_calls&&!provider_error);resets++;
        if(!stuck_after_reset)regs[(reg-MT_WFDMA0_RST+MT_WFDMA0_GLO_CFG)/4]&=~(BIT(1)|BIT(3));
    }
}
static u32 mt76_rr(struct mt7915_dev *d,u32 reg) { (void)d;assert(reg<sizeof(regs));return regs[reg/4]; }
static bool mt76_poll_msec(struct mt7915_dev *d,u32 reg,u32 mask,u32 val,unsigned int ms)
{ (void)d;assert(provider_calls);assert(ms==100);polls++;return (regs[reg/4]&mask)==val; }
static int airoha_npu_wlan_legacy_quiesce(struct airoha_npu *n,struct pci_dev *d)
{ assert(n&&!d->master==false);provider_calls++;return provider_error; }
static void airoha_npu_wlan_legacy_poison(struct airoha_npu *n,struct pci_dev *d)
{ (void)n;(void)d;poisons++; }
static void pci_clear_master(struct pci_dev *p) { p->master=false; }
static void setup(struct mt7915_dev *d,bool dual)
{
    static struct pci_dev pci,hifpci;static struct airoha_npu npu;static struct mt7915_hif hif;
    memset(d,0,sizeof(*d));memset(regs,0,sizeof(regs));memset(logbuf,0,sizeof(logbuf));
    pci.master=hifpci.master=true;hif.dev=&hifpci;d->mt76.dev=&pci;d->hif2=dual?&hif:NULL;
    d->mt76.mmio.npu=&npu;d->mt76.mmio.npu_legacy_rx=true;d->npu_legacy_configured=true;
    provider_error=provider_calls=poisons=polls=resets=barrier_step=diagnostics=0;
    stuck_after_reset=no_provider=false;
    for(int i=0;i<2;i++) {d->mt76.napi[i].enabled=true;d->mt76.q_rx[i].ndesc=256;}
    d->mt76.tx_worker.enabled=d->mt76.tx_napi.enabled=true;
}
'''


QUIESCE_TESTS = r'''
int main(void)
{
    struct mt7915_dev d;
    for(int dual=0;dual<2;dual++) {
        setup(&d,dual);regs[MT_WFDMA0_GLO_CFG/4]=BIT(0)|BIT(2);
        assert(!mt7915_npu_legacy_quiesce(&d,true));
        assert(!resets&&provider_calls==1&&!d.npu_legacy_configured&&!d.mt76.dev->master);
        assert(!d.mt76.mmio.npu_quarantined&&d.mt76.q_rx[0].stopped&&d.mt76.q_rx[1].stopped);
        setup(&d,dual);regs[MT_WFDMA0_GLO_CFG/4]=BIT(0)|BIT(1)|BIT(2)|BIT(3);
        if(dual)regs[(MT_WFDMA0_GLO_CFG+0x1000)/4]=BIT(0)|BIT(1)|BIT(2)|BIT(3);
        assert(!mt7915_npu_legacy_quiesce(&d,true));
        assert(resets==1+dual&&polls==2*(1+dual)&&!poisons&&!d.mt76.mmio.npu_quarantined);
        assert(!(regs[MT_WFDMA0_GLO_CFG/4]&(BIT(0)|BIT(1)|BIT(2)|BIT(3))));
        setup(&d,dual);regs[MT_WFDMA0_GLO_CFG/4]=BIT(1);stuck_after_reset=true;
        assert(mt7915_npu_legacy_quiesce(&d,true)==-ETIMEDOUT);
        assert(d.mt76.mmio.npu_quarantined&&d.npu_legacy_failed&&poisons==1);
        char expected[80];snprintf(expected,sizeof(expected),"wfdma_error=%d npu_error=0",-ETIMEDOUT);
        assert(strstr(logbuf,expected));
        int before=resets;assert(mt7915_npu_legacy_quiesce(&d,true)==-EIO&&resets==before);
        setup(&d,dual);regs[MT_WFDMA0_GLO_CFG/4]=BIT(1);provider_error=-ETIMEDOUT;
        assert(mt7915_npu_legacy_quiesce(&d,true)==-ETIMEDOUT);
        snprintf(expected,sizeof(expected),"npu_error=%d",-ETIMEDOUT);
        assert(!resets&&d.mt76.mmio.npu_quarantined&&strstr(logbuf,expected));
        setup(&d,dual);provider_error=-EIO;
        assert(mt7915_npu_legacy_quiesce(&d,true)==-EIO&&!resets&&poisons);
        setup(&d,dual);regs[MT_WFDMA0_GLO_CFG/4]=BIT(1);
        assert(mt7915_npu_legacy_quiesce(&d,false)==-ETIMEDOUT&&!resets&&poisons);
    }
    setup(&d,false);d.mt76.mmio.npu=NULL;
    assert(mt7915_npu_legacy_quiesce(&d,true)==-ENODEV&&!resets&&!poisons);
    puts("PASS: single/dual HIF stalled WFDMA resets once only after external DMA stop; residual busy/provider failures quarantine; cleanup never resets");
}
'''


def quiesce_test(source, cc):
    # Missing provider must still permit polling but not a reset.
    prefix = PREFIX.replace('assert(provider_calls);assert(ms==100);',
                            'assert(provider_calls||!d->mt76.mmio.npu);assert(ms==100);')
    prefix = prefix.replace('assert(n&&!d->master==false);', 'assert(n&&d->master);')
    body = '\n'.join(function(source, n) for n in (
        'mt7915_npu_legacy_wfdma_idle', 'mt7915_npu_legacy_wfdma_reset',
        'mt7915_npu_legacy_quiesce'))
    execute(cc, prefix + body + QUIESCE_TESTS)


EXTERNAL_STUBS = r'''
#define CONFIG_NET_AIROHA_WED 1
static int wed_error;
static bool mtk_wed_device_active(struct mtk_wed_device *wed) { return wed->active; }
static int mtk_wed_device_quiesce(struct mtk_wed_device *wed) { (void)wed;return wed_error; }
'''
EXTERNAL_TESTS = r'''
int main(void)
{
    struct mt7915_dev d;
    for(int full=0;full<2;full++)for(int failed=0;failed<2;failed++) {
        setup(&d,false);d.recovery.hw_full_reset=full;d.mt76.mmio.wed.active=true;
        regs[MT_WFDMA0_GLO_CFG/4]=BIT(1);wed_error=failed?-EIO:0;
        int ret=mt7915_dma_quiesce(&d);
        assert(resets==(full&&!failed));
        assert((ret==0)==(full&&!failed));
        if(ret)assert(d.mt76.mmio.wed.dma_quarantined&&d.mt76.mmio.npu_quarantined);
    }
    puts("PASS: actual DMA dispatcher permits fallback only for full reset with successful WED stop");
}
'''


def external_test(driver, cc):
    prefix = PREFIX.replace('assert(provider_calls);assert(ms==100);',
                            'assert(provider_calls||!d->mt76.mmio.npu);assert(ms==100);')
    prefix = prefix.replace('assert(n&&!d->master==false);', 'assert(n&&d->master);')
    source = (driver/'npu_legacy.c').read_text()
    body = '\n'.join(function(source, n) for n in (
        'mt7915_npu_legacy_wfdma_idle', 'mt7915_npu_legacy_wfdma_reset',
        'mt7915_npu_legacy_quiesce'))
    dispatcher = function((driver/'dma.c').read_text(), 'mt7915_dma_quiesce')
    dispatcher = dispatcher.split('\tif (!mtk_wed_device_active(wed))', 1)[0] + '\treturn 0;\n}\n'
    execute(cc, prefix + body + EXTERNAL_STUBS + dispatcher + EXTERNAL_TESTS)


ORDER_STUBS = r'''
static int quiesce_error,retired_tokens;
static bool mtk_wed_device_active(void *w) { (void)w;return true; }
static bool dev_is_pci(void *p) { (void)p;return true; }
static bool is_mt7915(struct mt76_dev *d) { (void)d;return false; }
static void mt76_wr(struct mt7915_dev *d,u32 r,u32 v) { (void)d;(void)r;(void)v; }
static void set_bit(int b,unsigned long *v) { *v|=1UL<<b; }
static void wake_up(void *p) { (void)p; }
static void mt76_txq_schedule_all(struct mt76_phy *p) { assert(p->state&BIT(MT76_RESET)); }
static void mt76_worker_disable(struct worker *w) { assert(w->enabled);w->enabled=false; }
static void napi_disable(struct napi_struct *n) { assert(n->enabled);n->enabled=false; }
static void napi_enable(struct napi_struct *n) { assert(!n->enabled);n->enabled=true; }
static int mt7915_dma_quiesce(struct mt7915_dev *d)
{
    assert(!d->mt76.tx_worker.enabled&&!d->mt76.tx_napi.enabled);
    assert(!d->mt76.napi[0].enabled&&!d->mt76.napi[1].enabled);
    assert(d->mphy.state&BIT(MT76_RESET));assert(!retired_tokens);
    return quiesce_error;
}
'''
ORDER_TESTS = r'''
int main(void)
{
    struct mt7915_dev d;setup(&d,false);quiesce_error=-ETIMEDOUT;retired_tokens=0;
    assert(mt7915_mac_restart(&d)==-ETIMEDOUT&&!retired_tokens);
    assert(d.recovery.wed_failed&&!d.mt76.tx_worker.enabled);
    assert(d.mt76.napi[0].enabled&&d.mt76.napi[1].enabled&&d.mt76.tx_napi.enabled);
    setup(&d,false);quiesce_error=0;retired_tokens=0;
    assert(!mt7915_mac_restart(&d)&&retired_tokens==1&&!d.recovery.wed_failed);
    puts("PASS: actual full-reset prefix stops software before quiesce; failed stop retains tokens and balances NAPI");
}
'''


def order_test(source, cc):
    body = function(source, 'mt7915_mac_restart')
    # Stop at the first memory-retirement point; hardware/firmware reboot is
    # covered by compilation and a subsequent device test, not faked here.
    body = body.split('\t/* token reinit */', 1)[0] + '\tretired_tokens++;\n\t(void)run_main; (void)run_ext; (void)phy2;\n\treturn 0;\n}\n'
    prefix = PREFIX.replace('struct mt76_phy { unsigned long state; };',
                            'struct mt76_phy { unsigned long state; void *priv; };')
    prefix = prefix.replace('int mcu;', 'struct {int wait;} mcu;')
    execute(cc, prefix + ORDER_STUBS + body + ORDER_TESTS)


STATUS_STUBS = r'''
struct seq_file { void *private;char output[4096]; };
struct mt7915_phy { struct mt7915_dev *dev; };
#define atomic64_read(p) (*(p))
static void __attribute__((format(printf,2,3))) seq_printf(struct seq_file *s,const char *fmt,...)
{ va_list args;va_start(args,fmt);vsnprintf(s->output+strlen(s->output),sizeof(s->output)-strlen(s->output),fmt,args);va_end(args); }
static void seq_puts(struct seq_file *s,const char *text) { seq_printf(s,"%s",text); }
'''
STATUS_TESTS = r'''
int main(void)
{
    struct mt7915_dev d;struct mt7915_phy phy={.dev=&d};struct seq_file s={.private=&phy};
    for(int state=0;state<3;state++) {
        setup(&d,false);memset(s.output,0,sizeof(s.output));
        d.npu_legacy_configured=state==0;d.npu_legacy_failed=state==2;d.mt76.mmio.npu_quarantined=state==2;
        d.mt76.q_rx[0].head=17;d.mt76.q_rx[0].tail=12;d.mt76.q_rx[0].stopped=state!=0;
        assert(!mt7915_offload_status_show(&s,NULL));
        assert(strstr(s.output,"rx_queue_view: host-software"));
        assert(strstr(s.output,"rx0_host_head: 17\nrx0_host_tail: 12"));
    }
    puts("PASS: live, recovering and quarantined status reads work with no NPU MMIO API present");
}
'''


def status_test(source, cc):
    prefix = PREFIX.replace('struct mt7915_phy { int unused; };', '')
    execute(cc, prefix + STATUS_STUBS + function(source, 'mt7915_offload_status_show') + STATUS_TESTS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mt76', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    driver = args.mt76/'mt7915'
    quiesce_test((driver/'npu_legacy.c').read_text(), args.cc)
    external_test(driver, args.cc)
    order_test((driver/'mac.c').read_text(), args.cc)
    status_test((driver/'debugfs.c').read_text(), args.cc)


if __name__ == '__main__':
    main()
