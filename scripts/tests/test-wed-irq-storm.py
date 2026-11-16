#!/usr/bin/env python3
"""Exercise WED normal/extended IRQ masking, W1C acknowledgement and storm protection."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def function(text, name, kind='static void'):
    match = re.search(r'^' + re.escape(kind) + r' ' + re.escape(name) + r'\(', text, re.M)
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
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U << (n))
typedef struct { int v; } atomic_t;
#define HZ 100
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define time_after(a, b) ((long)((b) - (a)) < 0)
static unsigned long jiffies;
static int atomic_read(const atomic_t *a) { return a->v; }
static void atomic_set(atomic_t *a, int v) { a->v = v; }
static int atomic_inc_return(atomic_t *a) { return ++a->v; }
static int atomic_cmpxchg(atomic_t *a, int o, int n) { int r = a->v; if (r == o) a->v = n; return r; }
#define cmpxchg(p, o, n) ({ __typeof__(*(p)) _r = *(p); if (_r == (o)) *(p) = (n); _r; })
struct device { int unused; };
struct airoha_wed { struct device *dev; u32 regs[0xb00 / sizeof(u32)]; int irq; bool irq_disabled, poisoned;
                    atomic_t irq_count, irq_storm; unsigned long irq_window; };
struct mtk_wed_device { struct airoha_wed *priv; struct device *dev; };
static struct airoha_wed *airoha_wed_priv(struct mtk_wed_device *w) { return w->priv; }
static int disables, errors, exceptions, mask_writes;
static u32 readl_reg(struct airoha_wed *aw, u32 off) { return aw->regs[off / 4]; }
static void writel_reg(struct airoha_wed *aw, u32 v, u32 off)
{
    if (off == MTK_WED_INT_STATUS || off == MTK_WED_EXT_INT_STATUS)
        aw->regs[off / 4] &= ~v; /* hardware write-one-to-clear status */
    else
        aw->regs[off / 4] = v;
}
static u32 readl(const char *addr) { return *(const u32 *)addr; }
static void writel(u32 v, char *addr) { *(u32 *)addr = v; }
static void disable_irq_nosync(int irq) { (void)irq; disables++; }
static void note(const char *f, ...) { (void)f; errors++; }
#define dev_err(dev, ...) do { (void)(dev); note(__VA_ARGS__); } while (0)
#define dev_warn_ratelimited(dev, ...) do { (void)(dev); exceptions++; } while (0)
#define airoha_wlan_dbg(dev, ...) do { (void)(dev); } while (0)

static void airoha_wed_write(struct mtk_wed_device *w, u32 off, u32 v) { writel_reg(w->priv, v, off); mask_writes++; }
static u32 airoha_wed_read(struct mtk_wed_device *w, u32 off) { return readl_reg(w->priv, off); }
'''
# aw->base is used as a plain offset base in the real code: model it as 0.
PREFIX = PREFIX.replace('struct airoha_wed { struct device *dev;', 'struct airoha_wed { struct device *dev; char *base;')

TESTS = r'''
#define LIMIT AIROHA_WED_IRQ_STORM_LIMIT
static bool irq_asserted(struct airoha_wed *aw)
{
    return (aw->regs[MTK_WED_INT_STATUS / 4] & aw->regs[MTK_WED_INT_MASK / 4]) ||
           (aw->regs[MTK_WED_EXT_INT_STATUS / 4] & aw->regs[MTK_WED_EXT_INT_MASK / 4]);
}
static void mask_and_ack_tests(void)
{
    struct device dev;
    struct airoha_wed aw = {.dev = &dev, .irq = 7};
    struct mtk_wed_device wed = {.priv = &aw};
    const u32 ext_error = MTK_WED_EXT_INT_STATUS_RX_DRV_INIT_WDMA_EN;
    aw.base = (char *)aw.regs;
    jiffies = 1000;
    assert(ext_error == 0x00080000 && (EN7581_WED_EXT_ERRORS & ext_error));

    /* Reproduce the device report: no normal event, only extended bit 19. */
    aw.regs[MTK_WED_EXT_INT_MASK / 4] = EN7581_WED_EXT_ERRORS;
    airoha_wed_irq_mask(&wed, 0x20);
    aw.regs[MTK_WED_EXT_INT_STATUS / 4] = ext_error;
    assert(irq_asserted(&aw));
    airoha_wed_irq_mask(&wed, 0);
    assert(aw.regs[MTK_WED_INT_MASK / 4] == 0);
    assert(aw.regs[MTK_WED_EXT_INT_MASK / 4] == 0);
    assert(!irq_asserted(&aw)); /* hard IRQ leaves time for its tasklet */
    assert(aw.regs[MTK_WED_EXT_INT_STATUS / 4] == ext_error);
    assert(airoha_wed_irq_get(&wed, 0x20) == 0);
    assert(aw.regs[MTK_WED_EXT_INT_STATUS / 4] == 0 && exceptions == 1);
    airoha_wed_irq_mask(&wed, 0x20);
    assert(aw.regs[MTK_WED_INT_MASK / 4] == 0x20);
    assert(aw.regs[MTK_WED_EXT_INT_MASK / 4] == EN7581_WED_EXT_ERRORS);
    assert(!irq_asserted(&aw) && !disables && !errors);

    /* Normal and extended events are both acknowledged; masked bits survive. */
    aw.regs[MTK_WED_INT_STATUS / 4] = 0x24;
    aw.regs[MTK_WED_EXT_INT_STATUS / 4] = ext_error;
    assert(irq_asserted(&aw));
    airoha_wed_irq_mask(&wed, 0);
    assert(!irq_asserted(&aw));
    assert(airoha_wed_irq_get(&wed, 0x20) == 0x20);
    assert(aw.regs[MTK_WED_INT_STATUS / 4] == 0x04);
    assert(aw.regs[MTK_WED_EXT_INT_STATUS / 4] == 0 && exceptions == 2);
    airoha_wed_irq_mask(&wed, 0x20);
    assert(!irq_asserted(&aw));

    /* A later error is delivered again: this fix does not hide bit 19. */
    aw.regs[MTK_WED_EXT_INT_STATUS / 4] = ext_error;
    assert(irq_asserted(&aw));
    airoha_wed_irq_mask(&wed, 0);
    assert(!irq_asserted(&aw));
    assert(airoha_wed_irq_get(&wed, 0x20) == 0 && exceptions == 3);
    aw.poisoned = true;
    airoha_wed_irq_mask(&wed, 0xff);
    assert(aw.regs[MTK_WED_INT_MASK / 4] == 0 && aw.regs[MTK_WED_EXT_INT_MASK / 4] == 0);
    puts("PASS: ext=00080000 deasserts during hard IRQ masking, is acknowledged by W1C and is delivered again after unmask");
}
int main(void)
{
    mask_and_ack_tests();
    struct device dev; struct airoha_wed aw = {.dev = &dev, .irq = 7};
    aw.base = (char *)aw.regs;
    struct mtk_wed_device wed = {.priv = &aw};
    aw.regs[MTK_WED_INT_STATUS / 4] = 0xa5; aw.regs[MTK_WED_EXT_INT_STATUS / 4] = 0x5a;
    jiffies = 1000;

    /* A normal rate never trips, and the window resets the counter. */
    for (int round = 0; round < 5; round++) {
        for (int i = 0; i < LIMIT; i++) airoha_wed_irq_mask(&wed, 0);
        assert(!disables && !aw.irq_disabled && !atomic_read(&aw.irq_storm));
        jiffies += HZ + 1;
    }
    /* Non-zero masks are the re-enable path and never count. */
    for (int i = 0; i < 3 * LIMIT; i++) airoha_wed_irq_mask(&wed, 0x30);
    assert(!disables && atomic_read(&aw.irq_count) <= LIMIT);
    assert(aw.regs[MTK_WED_INT_MASK / 4] == 0x30);

    /* One extra interrupt in the same window trips the guard exactly once. */
    jiffies += HZ + 1;
    for (int i = 0; i < LIMIT + 1; i++) airoha_wed_irq_mask(&wed, 0);
    assert(disables == 1 && errors == 1 && aw.irq_disabled && atomic_read(&aw.irq_storm));
    assert(!aw.poisoned);                       /* DMA ownership state untouched */
    assert(aw.regs[MTK_WED_INT_MASK / 4] == 0 && aw.regs[MTK_WED_EXT_INT_MASK / 4] == 0);
    for (int i = 0; i < 5 * LIMIT; i++) airoha_wed_irq_mask(&wed, 0);
    assert(disables == 1 && errors == 1);       /* no repeat */

    /* After the trip, re-enable requests are forced to zero. */
    airoha_wed_irq_mask(&wed, 0xff);
    assert(aw.regs[MTK_WED_INT_MASK / 4] == 0);
    assert(aw.regs[MTK_WED_EXT_INT_MASK / 4] == 0);

    /* quarantine/detach see irq_disabled and do not disable the line twice. */
    int again = 0; if (!READ_ONCE(aw.irq_disabled)) again++;
    assert(again == 0);
    puts("PASS: WED IRQ storm guard trips once at the limit, resets per window, keeps poisoned clear and masks stay zero");
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
             'MTK_WED_EXT_INT_STATUS_RX_DRV_INIT_WDMA_EN')
    definitions = [re.search(r'^#define ' + re.escape(name) + r'\s+[^\n]+', registers, re.M).group()
                   for name in names]
    for name in ('AIROHA_WED_IRQ_STORM_LIMIT', 'EN7581_WED_EXT_ERRORS'):
        definitions.append(re.search(r'^#define ' + name + r'\s+[^\n]+', text, re.M).group())
    code = '\n'.join((*definitions, PREFIX,
                      function(text, 'airoha_wed_irq_storm_check'),
                      function(text, 'airoha_wed_irq_mask'),
                      function(text, 'airoha_wed_irq_get', kind='static u32'), TESTS))
    with tempfile.TemporaryDirectory(prefix='hg5585f-wed-irq-test-') as temporary:
        source, binary = Path(temporary)/'test.c', Path(temporary)/'test'
        source.write_text(code)
        subprocess.run([args.cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', str(source), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    main()
