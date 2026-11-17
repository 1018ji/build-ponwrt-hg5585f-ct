#!/usr/bin/env python3
"""Execute real mt7915 WED initializers and check the ordinary AP sentinel."""
import argparse
import re
from pathlib import Path
import subprocess
import tempfile


def function(text, name):
    pos = text.index('u32 '+name+'(')
    start = text.rfind('\n', 0, pos)+1
    brace = text.index('{', pos)
    end, depth = brace+1, 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mt76', type=Path, required=True)
    args = parser.parse_args()
    native = function((args.mt76/'mt7915/mac.c').read_text(), 'mt7915_wed_init_buf')
    layout = re.search(r'struct mt76_connac_fw_txp \{.*?\} __packed __aligned\(4\);', (args.mt76/'mt76_connac.h').read_text(), re.S)[0]
    mmio = (args.mt76/'mt7915/mmio.c').read_text()
    airoha = function(mmio, 'mt7915_airoha_wed_init_buf')
    # Exercise the actual selector too: generic WED keeps its initializer.
    begin = mmio.index('\twed->wlan.init_buf =')
    end = mmio.index(';', begin)+1
    selector = mmio[begin:end]
    source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t dma_addr_t;
typedef uint16_t __le16;
typedef uint32_t __le32;
#define MT_TXD_SIZE 32
#define MT_TXD0_TX_BYTES 0xffffU
#define MT_TXD0_PKT_FMT (3U << 23)
#define MT_TX_TYPE_CT 0
#define MT_TXD1_LONG_FORMAT (1U << 31)
#define MT_TXD1_HDR_FORMAT (3U << 16)
#define MT_HDR_FORMAT_802_3 0
#define FIELD_PREP(mask,value) (((value) << __builtin_ctz(mask)) & (mask))
#define cpu_to_le16(v) ((uint16_t)(v))
#define cpu_to_le32(v) ((uint32_t)(v))
#define MT_TXP_MAX_BUF_NUM 6
#define __packed __attribute__((packed))
#define __aligned(x) __attribute__((aligned(x)))
'''+layout+r'''
'''+native+'\n'+airoha+r'''
static bool of_property_present(void *node, const char *property)
{ assert(!strcmp(property, "airoha,wed")); return !!node; }
struct fake_dev { struct { struct { void *of_node; } *dev; } mt76; };
struct fake_wed { struct {u32 (*init_buf)(void *, dma_addr_t, int);} wlan; };
static void select_buf(struct fake_dev *dev, struct fake_wed *wed)
{
'''+selector+r'''
}
int main(void)
{
    _Static_assert(sizeof(struct mt76_connac_fw_txp)==44, "TXP stride");
    _Static_assert(offsetof(struct mt76_connac_fw_txp,rept_wds_wcid)==5, "sentinel offset");
    const int tokens[] = {0,4095,4096,8191};
    for (unsigned int i=0;i<sizeof(tokens)/sizeof(tokens[0]);i++) {
        unsigned char regular[128], fixed[128];
        memset(regular,0xa5,sizeof(regular));memset(fixed,0xa5,sizeof(fixed));
        assert(mt7915_wed_init_buf(regular,0x90001000,tokens[i])==76);
        assert(mt7915_airoha_wed_init_buf(fixed,0x90001000,tokens[i])==76);
        struct mt76_connac_fw_txp *txp=(void *)(fixed+MT_TXD_SIZE);
        assert(txp->rept_wds_wcid==0x3ff);
        assert(txp->token==tokens[i] && txp->nbuf==1 && txp->buf[0]==0x9000104c);
        assert(((struct mt76_connac_fw_txp *)(regular+MT_TXD_SIZE))->rept_wds_wcid==0);
        for(unsigned int byte=0;byte<sizeof(fixed);byte++)
            if(byte!=37 && byte!=38) assert(fixed[byte]==regular[byte]);
        /* Hardware supplies the low eight WCID bits; the upper bits survive. */
        fixed[37]=0xff;fixed[36]=1;
        assert(txp->rept_wds_wcid==0x3ff && txp->bss_idx==1);
    }
    struct {void *of_node;} device={0};
    struct fake_dev dev={0};struct fake_wed wed={0};
    dev.mt76.dev=(void *)&device;
    select_buf(&dev,&wed);assert(wed.wlan.init_buf==mt7915_wed_init_buf);
    device.of_node=&device;
    select_buf(&dev,&wed);assert(wed.wlan.init_buf==mt7915_airoha_wed_init_buf);
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as temporary:
        path, binary = Path(temporary)/'test.c', Path(temporary)/'test'
        path.write_text(source)
        subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror',str(path),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True)
    print('PASS: real Airoha TXP initializer preserves 0x3ff across WCID rewrite, boundary tokens and DMA layout; generic WED selection preserved')


if __name__ == '__main__':
    main()
