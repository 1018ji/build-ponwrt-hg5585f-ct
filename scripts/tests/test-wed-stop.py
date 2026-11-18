#!/usr/bin/env python3
"""Execute the real WED stop functions with DMA and reset failure injection."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def function(text, name, kind):
    match = re.search(r'^'+re.escape(kind)+r'\s+'+name+r'\(', text, re.M)
    assert match, name
    brace = text.index('{', match.start())
    end, depth = brace+1, 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end]


MODEL = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
typedef uint16_t u16;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((UINT32_MAX>>(31-(h))) & (UINT32_MAX<<(l)))
#define U16_MAX UINT16_MAX
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define xchg(p,v) ({ __typeof__(*(p)) old=*(p);*(p)=(v);old; })
struct device { int unused; };
struct mutex { bool held; };
struct airoha_ppe_dev { struct { int (*flush_wed)(struct airoha_ppe_dev *,u16); } ops; };
struct airoha_wed {
    char *base,*wdma; struct device *dev; struct airoha_ppe_dev *ppe;
    struct mutex quiesce_lock; bool poisoned,irq_disabled; int irq;
    const char *last_stop_stage;
    int last_stop_error,last_dma_stop_error,last_flush_error;
    u32 stop_reset_attempts,stop_reset_successes;
};
struct mtk_wed_device { struct airoha_wed *priv; bool running,init_done,dma_quarantined; };
static u32 wed_regs[0xb00/4],wdma_regs[0x400/4];
static unsigned int reset_calls,irq_disables,flush_calls,index_writes,idle_calls;
static int before_stage,after_stage,reset_error,flush_error;
static bool unreadable;
static char messages[8192];
static struct airoha_wed *airoha_wed_priv(struct mtk_wed_device *wed) { return wed->priv; }
static void note(const char *format,...)
{
    va_list args;va_start(args,format);
    size_t used=strlen(messages);
    vsnprintf(messages+used,sizeof(messages)-used,format,args);va_end(args);
}
#define dev_err(dev,...) do { (void)(dev);note(__VA_ARGS__); } while(0)
#define dev_warn(dev,...) do { (void)(dev);note(__VA_ARGS__); } while(0)
#define dev_info(dev,...) do { (void)(dev);note(__VA_ARGS__); } while(0)
static u32 readl(const void *p) { return *(const u32 *)p; }
static void writel(u32 v,void *p)
{
    if(p==(void *)((char *)wdma_regs+MTK_WDMA_RESET_IDX))index_writes++;
    *(u32 *)p=v;
}
static u32 airoha_wed_read(struct mtk_wed_device *wed,u32 offset)
{ return readl(wed->priv->base+offset); }
static void airoha_wed_write(struct mtk_wed_device *wed,u32 offset,u32 value)
{ writel(value,wed->priv->base+offset); }
static void airoha_wed_rmw(struct mtk_wed_device *wed,u32 offset,u32 mask,u32 value)
{ airoha_wed_write(wed,offset,(airoha_wed_read(wed,offset)&~mask)|value); }
static void airoha_wed_irq_mask(struct mtk_wed_device *wed,u32 mask)
{
    airoha_wed_write(wed,MTK_WED_INT_MASK,mask);
    airoha_wed_write(wed,MTK_WED_EXT_INT_MASK,mask?0xffffffff:0);
}
static bool producers_enabled(void)
{
    return (wdma_regs[MTK_WDMA_GLO_CFG/4]&MTK_WDMA_GLO_CFG_RX_DMA_EN) ||
           (wed_regs[MTK_WED_GLO_CFG/4]&EN7581_WED_DMA_ENABLE) ||
           (wed_regs[MTK_WED_WPDMA_GLO_CFG/4]&EN7581_WED_DMA_ENABLE) ||
           (wed_regs[MTK_WED_WDMA_GLO_CFG/4]&BIT(2));
}
static int idle_result(unsigned int stage)
{
    idle_calls++;
    if(producers_enabled() || unreadable)return -ETIMEDOUT;
    assert(!wed_regs[MTK_WED_INT_MASK/4] && !wed_regs[MTK_WED_EXT_INT_MASK/4]);
    assert(!wdma_regs[MTK_WDMA_INT_MASK/4] && !wdma_regs[MTK_WDMA_INT_GRP2/4]);
    return (reset_calls?after_stage:before_stage)==(int)stage?-ETIMEDOUT:0;
}
static int airoha_wdma_rx_idle(struct airoha_wed *aw)
{ (void)aw;return idle_result(1); }
static int airoha_wed_poll(struct airoha_wed *aw,u32 reg,u32 mask)
{
    (void)aw;(void)mask;
    if(reg==MTK_WED_GLO_CFG)return idle_result(2);
    if(reg==MTK_WED_WPDMA_GLO_CFG)return idle_result(3);
    assert(reg==MTK_WED_WDMA_GLO_CFG);return idle_result(4);
}
static int airoha_wed_reset(struct mtk_wed_device *wed,u32 mask)
{
    assert(mask==MTK_WED_RESET_WED && !producers_enabled());reset_calls++;
    /* Successful reset can restore enable bits; the caller must disable again. */
    if(!reset_error)wed_regs[MTK_WED_GLO_CFG/4]=EN7581_WED_DMA_ENABLE;
    (void)wed;return reset_error;
}
static void mutex_lock(struct mutex *m) { assert(!m->held);m->held=true; }
static void mutex_unlock(struct mutex *m) { assert(m->held);m->held=false; }
static void disable_irq(int irq) { assert(irq==27);irq_disables++; }
static struct mtk_wed_device *active;
static int flush_wed(struct airoha_ppe_dev *ppe,u16 wcid)
{ (void)ppe;assert(wcid==U16_MAX && !active->running);flush_calls++;return flush_error; }
static void initialize(struct airoha_wed *aw,struct mtk_wed_device *wed,
                       struct airoha_ppe_dev *ppe)
{
    memset(aw,0,sizeof(*aw));memset(wed,0,sizeof(*wed));
    memset(wed_regs,0,sizeof(wed_regs));memset(wdma_regs,0,sizeof(wdma_regs));
    aw->base=(char *)wed_regs;aw->wdma=(char *)wdma_regs;aw->irq=27;aw->ppe=ppe;
    ppe->ops.flush_wed=flush_wed;wed->priv=aw;wed->running=wed->init_done=true;active=wed;
    reset_calls=irq_disables=flush_calls=index_writes=idle_calls=0;
    before_stage=after_stage=reset_error=flush_error=0;unreadable=false;messages[0]=0;
    wdma_regs[MTK_WDMA_GLO_CFG/4]=MTK_WDMA_GLO_CFG_RX_DMA_EN;
    wed_regs[MTK_WED_GLO_CFG/4]=EN7581_WED_DMA_ENABLE;
    wed_regs[MTK_WED_WPDMA_GLO_CFG/4]=EN7581_WED_DMA_ENABLE;
    wed_regs[MTK_WED_WDMA_GLO_CFG/4]=BIT(2);
    wed_regs[MTK_WED_INT_MASK/4]=wed_regs[MTK_WED_EXT_INT_MASK/4]=0xffffffff;
    wdma_regs[MTK_WDMA_INT_MASK/4]=wdma_regs[MTK_WDMA_INT_GRP2/4]=0xffffffff;
}
'''

TESTS = r'''
int main(void)
{
    struct airoha_wed aw;struct mtk_wed_device wed;struct airoha_ppe_dev ppe;
    initialize(&aw,&wed,&ppe);
    assert(!airoha_wed_quiesce(&wed));
    assert(!reset_calls && !aw.poisoned && !wed.running && wed.init_done);
    assert(!strcmp(aw.last_stop_stage,"idle") && idle_calls==4 && !index_writes);
    puts("PASS: disable all producers before polling; normal stop needs no reset or index writes");
    for(int stage=1;stage<=4;stage++) {
        initialize(&aw,&wed,&ppe);before_stage=stage;
        assert(!airoha_wed_quiesce(&wed));
        assert(reset_calls==1 && aw.stop_reset_attempts==1 && aw.stop_reset_successes==1);
        assert(!aw.poisoned && !wed.init_done && !index_writes);
        assert(!strcmp(aw.last_stop_stage,"idle") && strstr(messages,"stop_recovered"));
    }
    puts("PASS: one bounded WED reset retries all four DMA stages before permitting recovery");
    for(int stage=1;stage<=4;stage++) {
        initialize(&aw,&wed,&ppe);before_stage=1;after_stage=stage;
        assert(airoha_wed_stop_sync(&wed)==-ETIMEDOUT);
        assert(wed.dma_quarantined && aw.poisoned && aw.irq_disabled && irq_disables==1);
        assert(aw.last_dma_stop_error==-ETIMEDOUT && aw.last_stop_error==-ETIMEDOUT);
        assert(!aw.stop_reset_successes && reset_calls==1 && !index_writes);
        const char *names[]={"wdma_rx","wed_dma","wpdma_drv","wdma_drv"};
        assert(!strcmp(aw.last_stop_stage,names[stage-1]) && strstr(messages,"stop_failed"));
    }
    puts("PASS: busy external WDMA or WED drivers after reset retain quarantine and DMA ownership");
    initialize(&aw,&wed,&ppe);before_stage=1;reset_error=-EIO;
    assert(airoha_wed_stop_sync(&wed)==-EIO);
    assert(!strcmp(aw.last_stop_stage,"wed_reset") && !aw.stop_reset_successes && !index_writes);
    initialize(&aw,&wed,&ppe);unreadable=true;
    assert(airoha_wed_stop_sync(&wed)==-ETIMEDOUT && wed.dma_quarantined);
    assert(!aw.stop_reset_successes && !index_writes);
    initialize(&aw,&wed,&ppe);flush_error=-EBUSY;
    assert(airoha_wed_stop_sync(&wed)==-EBUSY);
    assert(flush_calls==1 && !aw.last_dma_stop_error && aw.last_flush_error==-EBUSY);
    assert(wed.dma_quarantined && !reset_calls && !aw.quiesce_lock.held);
    initialize(&aw,&wed,&ppe);wed.dma_quarantined=true;
    assert(airoha_wed_stop_sync(&wed)==-EIO && wed.dma_quarantined && aw.poisoned);
    puts("PASS: reset failure, unreadable status, PPE flush failure and previous faults cannot report success");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    text = (args.baseline or args.kernel/'drivers/net/ethernet/airoha/airoha_wed.c').read_text()
    registers = (args.kernel/'drivers/net/ethernet/mediatek/mtk_wed_regs.h').read_text()
    names = ('MTK_WDMA_RESET_IDX', 'MTK_WDMA_GLO_CFG', 'MTK_WDMA_GLO_CFG_RX_DMA_EN',
             'MTK_WDMA_INT_MASK', 'MTK_WDMA_INT_GRP2', 'MTK_WED_RESET_WED',
             'MTK_WED_INT_MASK', 'MTK_WED_EXT_INT_MASK', 'MTK_WED_GLO_CFG',
             'MTK_WED_WPDMA_GLO_CFG', 'MTK_WED_WDMA_GLO_CFG',
             'MTK_WED_WPDMA_INT_TRIGGER', 'MTK_WED_WDMA_INT_TRIGGER')
    defines = [re.search(r'^#define '+name+r'\b[^\n]*', registers, re.M)[0] for name in names]
    defines += [re.search(r'^#define '+name+r'\b[^\n]*', text, re.M)[0]
                for name in ('EN7581_WED_DMA_ENABLE', 'EN7581_WED_DMA_BUSY')]
    functions = []
    for name, kind in (('airoha_wed_disable_dma', 'static void'),
                       ('airoha_wed_dma_idle', 'static int')):
        if name+'(' in text:
            functions.append(function(text, name, kind))
    functions += [function(text, name, 'static int') for name in
                  ('airoha_wed_quiesce', 'airoha_wed_stop_sync')]
    with tempfile.TemporaryDirectory(prefix='hg5585f-wed-stop-') as temporary:
        source, binary = Path(temporary)/'stop.c', Path(temporary)/'stop'
        source.write_text(MODEL.split('struct device', 1)[0] + '\n'.join(defines) +
                          '\nstruct device' + MODEL.split('struct device', 1)[1] +
                          '\n'.join(functions) + TESTS)
        subprocess.run([args.cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', str(source), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    main()
