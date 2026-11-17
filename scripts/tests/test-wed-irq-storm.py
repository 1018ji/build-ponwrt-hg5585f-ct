#!/usr/bin/env python3
"""Execute WED IRQ classification, acknowledgement and fault draining in C."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def function(text, name, kind='static void'):
    match = re.search(r'^' + re.escape(kind) + r' ' + name + r'\(', text, re.M)
    assert match, name
    brace = text.index('{', match.start())
    end, depth = brace + 1, 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end]


PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
typedef uint16_t u16;
typedef struct { int v; } atomic_t;
#define BIT(n) (1U << (n))
#define HZ 100
#define MTK_WED_TX_QUEUES 2
#define U16_MAX UINT16_MAX
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define xchg(p,v) ({ __typeof__(*(p)) old=*(p); *(p)=(v); old; })
#define time_after(a,b) ((long)((b)-(a))<0)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
static unsigned long jiffies;
static int atomic_read(const atomic_t *a) { return a->v; }
static void atomic_set(atomic_t *a,int v) { a->v=v; }
static int atomic_inc_return(atomic_t *a) { return ++a->v; }
static void atomic_inc(atomic_t *a) { a->v++; }
static int atomic_cmpxchg(atomic_t *a,int old,int v)
{ int was=a->v; if(was==old)a->v=v; return was; }
struct device { int unused; };
struct work_struct { bool queued; };
struct mutex { bool held; };
static struct mutex airoha_wed_lock;
struct mtk_wed_device;
struct airoha_ppe_dev {
    bool enabled;
    struct { int (*flush_wed)(struct airoha_ppe_dev *,u16);
             int (*set_wed)(struct airoha_ppe_dev *,bool); } ops;
};
struct airoha_wed {
    struct device *dev;
    char *base;
    u32 regs[0xb00/4];
    struct mtk_wed_device *wlan;
    struct airoha_ppe_dev *ppe;
    int irq;
    bool irq_disabled,poisoned,ppe_configured;
    atomic_t irq_count,irq_error_count,irq_storm;
    unsigned long irq_window;
    u32 irq_progress[MTK_WED_TX_QUEUES*2];
    u32 irq_last_status,irq_last_errors;
    struct work_struct irq_fault_work;
    struct mutex quiesce_lock;
};
struct mtk_wed_device {
    struct airoha_wed *priv;
    struct device *dev;
    bool running,dma_quarantined;
};
static struct airoha_wed *airoha_wed_priv(struct mtk_wed_device *w)
{ return w->priv; }
static unsigned int disables,errors,exceptions,scheduled,mib_reads;
static unsigned int flushes,drains,step;
static int flush_error,drain_error;
static bool expect_fault_order;
static u32 readl(const char *p) { mib_reads++;return *(const u32 *)p; }
static void writel(u32 v,char *p) { *(u32 *)p=v; }
static void disable_irq_nosync(int irq) { assert(irq==27);disables++; }
static void disable_irq(int irq) { assert(irq==27);disables++; }
static bool schedule_work(struct work_struct *work)
{ assert(!work->queued);work->queued=true;scheduled++;return true; }
static void mutex_lock(struct mutex *m) { assert(!m->held);m->held=true; }
static void mutex_unlock(struct mutex *m) { assert(m->held);m->held=false; }
static void note(const char *format,...) { (void)format;errors++; }
#define dev_err(dev,...) do { (void)(dev);note(__VA_ARGS__); } while(0)
#define dev_warn_ratelimited(dev,...) do { (void)(dev);exceptions++; } while(0)
#define airoha_wlan_dbg(dev,...) do { (void)(dev); } while(0)
static u32 airoha_wed_read(struct mtk_wed_device *w,u32 off)
{ return w->priv->regs[off/4]; }
static void airoha_wed_write(struct mtk_wed_device *w,u32 off,u32 value)
{
    if(off==MTK_WED_INT_STATUS || off==MTK_WED_EXT_INT_STATUS)
        w->priv->regs[off/4]&=~value;
    else w->priv->regs[off/4]=value;
}
static int flush_wed(struct airoha_ppe_dev *ppe,u16 wcid)
{
    assert(wcid==U16_MAX);flushes++;
    if(expect_fault_order)assert(!ppe->enabled && step++==1);
    return flush_error;
}
static int set_wed(struct airoha_ppe_dev *ppe,bool enabled)
{
    assert(!enabled && airoha_wed_lock.held);
    assert(step++==0);ppe->enabled=enabled;return 0;
}
static int airoha_wed_quiesce(struct mtk_wed_device *wed)
{
    assert(wed->priv->quiesce_lock.held);drains++;
    if(expect_fault_order)assert(step++==2);
    wed->running=false;return drain_error;
}
enum tc_setup_type { TC_SETUP_CLSFLOWER,TC_OTHER };
enum { FLOW_CLS_REPLACE,FLOW_CLS_DESTROY };
struct flow_cls_offload { int command; };
static int callbacks;
static int airoha_ppe_dev_setup_tc_block_cb(struct airoha_ppe_dev *ppe,void *data)
{ (void)ppe;(void)data;callbacks++;return 0; }
'''

TESTS = r'''
static void initialize(struct airoha_wed *aw,struct mtk_wed_device *wed,
                       struct airoha_ppe_dev *ppe)
{
    memset(aw,0,sizeof(*aw));memset(wed,0,sizeof(*wed));
    aw->base=(char *)aw->regs;aw->irq=27;aw->wlan=wed;aw->ppe=ppe;
    aw->ppe_configured=true;wed->priv=aw;wed->running=true;
    *ppe=(struct airoha_ppe_dev){.enabled=true,
        .ops={.flush_wed=flush_wed,.set_wed=set_wed}};
    disables=errors=exceptions=scheduled=mib_reads=flushes=drains=step=0;
    flush_error=drain_error=0;expect_fault_order=false;jiffies=1000;
}
static bool asserted(struct airoha_wed *aw)
{
    return (aw->regs[MTK_WED_INT_STATUS/4]&aw->regs[MTK_WED_INT_MASK/4]) ||
           (aw->regs[MTK_WED_EXT_INT_STATUS/4]&aw->regs[MTK_WED_EXT_INT_MASK/4]);
}
static void masking_tests(void)
{
    struct airoha_wed aw;struct mtk_wed_device wed;struct airoha_ppe_dev ppe;
    initialize(&aw,&wed,&ppe);
    u32 error=MTK_WED_EXT_INT_STATUS_RX_DRV_INIT_WDMA_EN;
    airoha_wed_irq_mask(&wed,0x20);aw.regs[MTK_WED_EXT_INT_STATUS/4]=error;
    assert(error==0x80000 && asserted(&aw));
    airoha_wed_irq_mask(&wed,0);assert(!asserted(&aw));
    assert(aw.regs[MTK_WED_EXT_INT_STATUS/4]==error);
    assert(!airoha_wed_irq_get(&wed,0x20));
    assert(!aw.regs[MTK_WED_EXT_INT_STATUS/4] && exceptions==1);
    assert(aw.irq_count.v==1); /* one serviced cause, not a mask operation */
    aw.regs[MTK_WED_INT_STATUS/4]=0x24;
    aw.regs[MTK_WED_EXT_INT_STATUS/4]=error;
    assert(airoha_wed_irq_get(&wed,0x20)==0x20);
    assert(aw.regs[MTK_WED_INT_STATUS/4]==4 && exceptions==2);
    for(int n=0;n<3*AIROHA_WED_IRQ_STORM_LIMIT;n++) {
        airoha_wed_irq_mask(&wed,0);airoha_wed_irq_mask(&wed,0);
    }
    assert(aw.irq_count.v==2 && !scheduled && !disables);
    aw.regs[MTK_WED_INT_STATUS/4]=0;
    for(int n=0;n<3*AIROHA_WED_IRQ_STORM_LIMIT;n++)
        assert(!airoha_wed_irq_get(&wed,0x20));
    assert(aw.irq_count.v==2 && !scheduled && !disables);
    puts("PASS: W1C/masking retained; repeated masks and native-only service do not count as WED interrupts");
}
static void progress_tests(void)
{
    struct airoha_wed aw;struct mtk_wed_device wed;struct airoha_ppe_dev ppe;
    initialize(&aw,&wed,&ppe);
    for(int n=0;n<3*AIROHA_WED_IRQ_STORM_LIMIT;n++) {
        aw.regs[MTK_WED_INT_STATUS/4]=0x20;
        aw.regs[MTK_WED_WDMA_RX_PROCESSED_MIB(1)/4]++;
        airoha_wed_irq_mask(&wed,0); /* hard IRQ */
        airoha_wed_irq_mask(&wed,0); /* tasklet */
        assert(airoha_wed_irq_get(&wed,0x20)==0x20);
    }
    assert(!scheduled && !disables && !wed.dma_quarantined);
    assert(mib_reads<32); /* no per-packet progress MMIO */
    initialize(&aw,&wed,&ppe);
    for(int n=0;n<AIROHA_WED_IRQ_STORM_LIMIT;n++) {
        aw.regs[MTK_WED_INT_STATUS/4]=0x20;
        assert(airoha_wed_irq_get(&wed,0x20)==0x20);
    }
    jiffies+=HZ+1;aw.regs[MTK_WED_INT_STATUS/4]=0x20;
    assert(airoha_wed_irq_get(&wed,0x20)==0x20 && aw.irq_count.v==1);
    assert(!scheduled && !disables);
    /* High-rate hardware errors still stop even if data counters move. */
    initialize(&aw,&wed,&ppe);
    for(int n=0;n<AIROHA_WED_IRQ_STORM_LIMIT+1;n++) {
        aw.regs[MTK_WED_EXT_INT_STATUS/4]=MTK_WED_EXT_INT_STATUS_RX_DRV_INIT_WDMA_EN;
        aw.regs[MTK_WED_WDMA_RX_PROCESSED_MIB(0)/4]++;
        airoha_wed_irq_get(&wed,0x20);
    }
    assert(scheduled==1 && disables==1 && wed.dma_quarantined);
    assert(aw.irq_error_count.v==AIROHA_WED_IRQ_STORM_LIMIT+1);
    initialize(&aw,&wed,&ppe);
    /* Progress in either band and counter wrap are accepted. */
    for(unsigned int band=0;band<2;band++) {
        aw.regs[MTK_WED_TX_MIB(band)/4]=UINT32_MAX;
        assert(airoha_wed_irq_progress(&aw));
        aw.regs[MTK_WED_TX_MIB(band)/4]=0;
        assert(airoha_wed_irq_progress(&aw));
    }
    puts("PASS: progressing traffic exceeds the old mask threshold without quarantine; windows and MIB wrap handled");
}
static void fault_tests(void)
{
    struct airoha_wed aw;struct mtk_wed_device wed;struct airoha_ppe_dev ppe;
    struct flow_cls_offload replace={.command=FLOW_CLS_REPLACE};
    struct flow_cls_offload destroy={.command=FLOW_CLS_DESTROY};
    initialize(&aw,&wed,&ppe);
    for(int n=0;n<AIROHA_WED_IRQ_STORM_LIMIT+1;n++) {
        aw.regs[MTK_WED_EXT_INT_STATUS/4]=MTK_WED_EXT_INT_STATUS_RX_DRV_INIT_WDMA_EN;
        assert(!airoha_wed_irq_get(&wed,0x20));
    }
    assert(scheduled==1 && disables==1 && aw.irq_disabled && wed.dma_quarantined);
    assert(aw.irq_last_errors==MTK_WED_EXT_INT_STATUS_RX_DRV_INIT_WDMA_EN);
    assert(airoha_wed_setup_cb(TC_SETUP_CLSFLOWER,&replace,&wed)==-EOPNOTSUPP);
    assert(!airoha_wed_setup_cb(TC_SETUP_CLSFLOWER,&destroy,&wed));
    for(int n=0;n<3*AIROHA_WED_IRQ_STORM_LIMIT;n++) {
        aw.regs[MTK_WED_INT_STATUS/4]=0x20;
        assert(!airoha_wed_irq_get(&wed,0x20));
        airoha_wed_irq_mask(&wed,0xff);
    }
    assert(scheduled==1 && disables==1 && !asserted(&aw));
    expect_fault_order=true;airoha_wed_irq_fault_work(&aw.irq_fault_work);
    assert(step==3 && flushes==1 && drains==1 && disables==1);
    assert(!ppe.enabled && !aw.ppe_configured && !wed.running && aw.poisoned);
    assert(wed.dma_quarantined && !airoha_wed_lock.held && !aw.quiesce_lock.held);
    /* A detached consumer is never dereferenced by deferred work. */
    aw.wlan=NULL;airoha_wed_irq_fault_work(&aw.irq_fault_work);
    assert(flushes==1 && drains==1 && !airoha_wed_lock.held);
    puts("PASS: repeated no-progress errors schedule one quarantine; gate closes before flush and DMA drain; deletes remain allowed");
}
static void drain_tests(void)
{
    struct airoha_wed aw;struct mtk_wed_device wed;struct airoha_ppe_dev ppe;
    for(int scenario=0;scenario<4;scenario++) {
        initialize(&aw,&wed,&ppe);
        if(scenario==1)aw.poisoned=true;
        if(scenario==2)flush_error=-ETIMEDOUT;
        if(scenario==3)drain_error=-ETIMEDOUT;
        int err=airoha_wed_stop_sync(&wed);
        assert(flushes==1 && drains==1 && !wed.running && !aw.quiesce_lock.held);
        assert((err==0)==(scenario==0));
        assert(wed.dma_quarantined==(scenario!=0));
        assert(aw.poisoned==(scenario!=0));
    }
    puts("PASS: poisoned/failed paths still invalidate and drain; failed ownership stays quarantined");
}
int main(void)
{
    masking_tests();progress_tests();fault_tests();drain_tests();
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    text = (args.kernel/'drivers/net/ethernet/airoha/airoha_wed.c').read_text()
    registers = (args.kernel/'drivers/net/ethernet/mediatek/mtk_wed_regs.h').read_text()
    names = ('MTK_WED_INT_STATUS', 'MTK_WED_INT_MASK',
             'MTK_WED_EXT_INT_STATUS', 'MTK_WED_EXT_INT_MASK',
             'MTK_WED_EXT_INT_STATUS_RX_DRV_INIT_WDMA_EN',
             'MTK_WED_TX_MIB', 'MTK_WED_WDMA_RX_PROCESSED_MIB')
    definitions = [re.search(r'^#define ' + name + r'\b[^\n]+', registers, re.M).group()
                   for name in names]
    for name in ('AIROHA_WED_IRQ_STORM_LIMIT', 'EN7581_WED_EXT_ERRORS'):
        definitions.append(re.search(r'^#define ' + name + r'\s+[^\n]+', text, re.M).group())
    functions = [('airoha_wed_irq_progress', 'static bool'),
                 ('airoha_wed_irq_storm_check', 'static void'),
                 ('airoha_wed_irq_mask', 'static void'),
                 ('airoha_wed_irq_get', 'static u32'),
                 ('airoha_wed_stop_sync', 'static int'),
                 ('airoha_wed_irq_fault_work', 'static void'),
                 ('airoha_wed_setup_cb', 'static int')]
    code = '\n'.join((*definitions, PREFIX,
                      *(function(text, name, kind) for name, kind in functions), TESTS))
    with tempfile.TemporaryDirectory(prefix='hg5585f-wed-irq-test-') as temporary:
        source, binary = Path(temporary)/'test.c', Path(temporary)/'test'
        source.write_text(code)
        subprocess.run([args.cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', str(source), '-o', str(binary)],check=True)
        subprocess.run([str(binary)],check=True)


if __name__ == '__main__':
    main()
