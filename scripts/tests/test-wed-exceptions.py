#!/usr/bin/env python3
"""Execute bounded WED diagnostic decoding and first-error capture in C."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def function(text, name, kind='static void'):
    start = re.search(r'^'+re.escape(kind)+r'\s+'+name+r'\(', text, re.M)
    assert start, name
    brace = text.index('{', start.start())
    end, depth = brace+1, 1
    while depth:
        depth += (text[end] == '{')-(text[end] == '}')
        end += 1
    return text[start.start():end]


PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef unsigned long long u64;
typedef uint32_t __le32;
typedef uint64_t dma_addr_t;
typedef unsigned int spinlock_t;
#define BIT(n) (1U<<(n))
#define MTK_WED_TX_QUEUES 2
#define PAGE_SIZE 4096
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define READ_ONCE(x) (x)
#define min_t(t,a,b) ((t)(a)<(t)(b)?(t)(a):(t)(b))
#define le32_to_cpu(x) (x)
#define upper_32_bits(x) ((u64)(x)>>32)
#define lower_32_bits(x) ((u32)(x))
#define dma_rmb() ((void)0)
#define spin_lock_irqsave(lock,flags) do { flags=0;assert(!*(lock));*(lock)=1; } while(0)
#define spin_unlock_irqrestore(lock,flags) do { (void)flags;assert(*(lock));*(lock)=0; } while(0)
#define struct_group(name,...) union { struct { __VA_ARGS__ }; struct { __VA_ARGS__ } name; }
static u16 get_unaligned_le16(const void *p)
{ const u8 *b=p;return b[0]|((u16)b[1]<<8); }
static u32 get_unaligned_le32(const void *p)
{ const u8 *b=p;return b[0]|((u32)b[1]<<8)|((u32)b[2]<<16)|((u32)b[3]<<24); }
static void put16(void *p,u16 v) { u8 *b=p;b[0]=v;b[1]=v>>8; }
static void put32(void *p,u32 v) { u8 *b=p;put16(b,v);put16(b+2,v>>16); }
'''

MIDDLE = r'''
struct mtk_wed_ring { void *desc;dma_addr_t desc_phys;unsigned int desc_size;int size;void *wpdma; };
struct airoha_wed_buffer { void *data;dma_addr_t dma; };
struct airoha_wed {
    char *base;
    struct airoha_wed_diag *diag;
    struct airoha_wed_buffer *buffers;
    unsigned int num_pages;
};
struct mtk_wed_device {
    bool airoha;
    struct airoha_wed *priv;
    struct { unsigned int nbuf,token_start; } wlan;
    struct mtk_wed_ring txfree_ring,rx_wdma[2],tx_ring[2];
};
static struct airoha_wed *airoha_wed_priv(struct mtk_wed_device *wed) { return wed->priv; }
static unsigned int reads,writes;
static u64 now;
static u64 ktime_get_mono_fast_ns(void) { return ++now; }
static u32 readl(const void *p) { reads++;return *(const u32 *)p; }
'''

TESTS = r'''
static void decode_tests(void)
{
    struct airoha_wed_free_record r;
    u8 data[1024]={0};
    put16(data,24);put16(data+2,4);put32(data+4,4U<<16);
    put32(data+8,BIT(31)|(3U<<14));put32(data+12,BIT(30));
    put32(data+16,4096U<<15);put32(data+20,8191U|(8192U<<15));
    airoha_wed_diag_decode(&r,data,24,false,0,4096,4096,4096);
    assert(r.version==4 && r.advertised==4 && r.decoded==4);
    assert(r.cpu_tokens==1 && r.wed_tokens==2 && r.unexpected==1);
    assert(r.station_words==1 && r.mpdu_words==1 && !r.malformed && !r.truncated);
    assert(r.token[0]==0 && r.token[1]==4096 && r.token[2]==8191 && r.token[3]==8192);
    for(unsigned int len=0;len<8;len++) {
        airoha_wed_diag_decode(&r,data,len,false,0,4096,4096,4096);
        assert(r.malformed && !r.decoded);
    }
    memset(data,0,sizeof(data));put16(data+2,100);put32(data+4,4U<<16);
    airoha_wed_diag_decode(&r,data,sizeof(data),false,0,4096,4096,4096);
    assert(!r.truncated && !r.malformed && r.raw_words==256 && r.decoded==100);
    assert(r.saved_tokens==16);
    airoha_wed_diag_decode(&r,data,12,false,0,4096,4096,4096);
    assert(r.malformed && !r.truncated);
    memset(data,0,sizeof(data));put16(data+2,1);put32(data+4,4U<<16);
    put32(data+8,4096|(0x7fffU<<15));
    airoha_wed_diag_decode(&r,data,12,false,0,4096,4096,4096);
    assert(r.decoded==1 && r.wed_tokens==1 && !r.malformed);
    put32(data+4,3U<<16);put32(data+8,4096U<<16);
    airoha_wed_diag_decode(&r,data,12,false,0,4096,4096,4096);
    assert(r.version==3 && r.wed_tokens==1 && !r.malformed);
    put32(data+4,7U<<16);
    airoha_wed_diag_decode(&r,data,12,false,0,4096,4096,4096);
    assert(r.unsupported && !r.decoded);
    memset(data,0,sizeof(data));put16(data+2,3);
    put16(data+8,0);put16(data+10,4096);put16(data+12,65535);
    airoha_wed_diag_decode(&r,data,14,true,0,4096,4096,4096);
    assert(r.version==0 && r.decoded==3 && r.cpu_tokens==1 && r.wed_tokens==1 && r.unexpected==1);
    puts("PASS: real bounded parser handles v4 pairs/markers/sentinels, v0, legacy, short/large/malformed records and token boundaries");
}
static void capture_tests(void)
{
    u32 regs[0xb00/4]={0},native[4]={0};
    u32 descriptors[4][4]={{1,2,3,4},{5,6,7,8},{9,10,11,12},{13,14,15,16}};
    u8 page[4096]={0},data[12]={0};
    struct airoha_wed_diag *diag=calloc(1,sizeof(*diag));
    struct airoha_wed_buffer buffer={.data=page,.dma=0x90000000};
    struct airoha_wed aw={.base=(char *)regs,.diag=diag,.buffers=&buffer,.num_pages=1};
    struct mtk_wed_device wed={.airoha=true,.priv=&aw,.wlan={.token_start=4096,.nbuf=2}};
    wed.txfree_ring=(struct mtk_wed_ring){.desc=descriptors,.desc_phys=0x88000000,.desc_size=16,.size=4,.wpdma=native};
    regs[MTK_WED_RING_RX(1)/4]=0x88000000;regs[(MTK_WED_RING_RX(1)+4)/4]=4;
    put16(data+2,1);put32(data+4,4U<<16);put32(data+8,4096|(0x7fffU<<15));
    put16(page+34,4096);page[39]=1;put32(page+40,0x9000004c);put16(page+64,54);
    page[76]=0xea;
    airoha_wed_diag_txfree(&wed,data,sizeof(data),false,0,4096);
    assert(diag->state.notifications==1 && !writes);
    regs[MTK_WED_WDMA_RX_PROCESSED_MIB(1)/4]=123;
    airoha_wed_diag_irq(&wed,0x20,BIT(1));
    assert(diag->state.count[1]==1 && diag->state.first[0].valid && !diag->state.first[1].valid);
    assert(diag->state.first_wdma[1][1]==123 && diag->state.first[0].host_before.sequence==1);
    assert(diag->state.first[0].buffers[0].payload_valid && diag->state.first[0].buffers[0].payload[0]==0xea);
    struct airoha_wed_ring_record *ring=&diag->state.first[0].rings[0];
    assert(ring->valid && ring->index[0]==2 && ring->index[3]==1 && ring->descriptor[0][0]==9);
    u64 stamp=diag->state.first[0].ns;
    regs[MTK_WED_WDMA_RX_PROCESSED_MIB(1)/4]=456;
    for(unsigned int i=0;i<5000;i++)airoha_wed_diag_irq(&wed,0,BIT(1));
    assert(diag->state.count[1]==5001 && diag->state.first[0].ns==stamp);
    assert(diag->state.last_wdma[1][1]==456);
    airoha_wed_diag_irq(&wed,0,BIT(18));
    assert(diag->state.count[18]==1 && diag->state.first[1].valid && diag->state.first[1].ns>stamp);
    airoha_wed_diag_txfree(&wed,data,sizeof(data),false,0,4096);
    assert(diag->state.first[0].host_after.sequence==2 && diag->state.first[1].host_after.sequence==2);
    airoha_wed_diag_txfree(&wed,data,sizeof(data),false,0,4096);
    assert(diag->state.first[0].host_after.sequence==2 && diag->state.latest.sequence==3);
    /* Invalid geometry and pointers are recorded, never followed. */
    struct airoha_wed_ring_record invalid={0};
    wed.txfree_ring.desc=(void *)1;regs[(MTK_WED_RING_RX(1)+4)/4]=5;
    airoha_wed_diag_ring(&aw,&invalid,&wed.txfree_ring,MTK_WED_RING_RX(1));
    assert(!invalid.valid);
    struct airoha_wed_buffer_record bad={0};
    put32(page+40,1);
    airoha_wed_diag_buffer(&wed,&bad,0);
    assert(bad.valid && !bad.payload_valid);
    memset(&bad,0,sizeof(bad));airoha_wed_diag_buffer(&wed,&bad,2);
    assert(!bad.valid && !writes);
    free(diag);
    puts("PASS: independent first snapshots survive an error flood; pool/ring bounds, before/after host records and read-only capture verified");
}
int main(void) { decode_tests();capture_tests();return 0; }
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    args = parser.parse_args()
    driver = args.kernel/'drivers/net/ethernet/airoha'
    text = (driver/'airoha_wed.c').read_text()
    header = (driver/'airoha_wed_diag.h').read_text()
    registers = (args.kernel/'drivers/net/ethernet/mediatek/mtk_wed_regs.h').read_text()
    names = ('MTK_WED_CTRL', 'MTK_WED_TX_BM_CTRL', 'MTK_WED_TX_BM_BASE',
             'MTK_WED_TX_BM_INIT_PTR', 'MTK_WED_TX_BM_BUF_LEN', 'MTK_WED_TX_BM_INTF',
             'MTK_WED_GLO_CFG', 'MTK_WED_WPDMA_GLO_CFG', 'MTK_WED_RING_RX',
             'MTK_WED_WDMA_RX_PROCESSED_MIB')
    definitions = [re.search(r'^#define '+name+r'\b[^\n]+', registers, re.M)[0] for name in names]
    for name in ('EN7581_WED_CTRL2', 'EN7581_WED_WPDMA_CTRL', 'EN7581_WED_WPDMA_OFFSET0',
                 'EN7581_WED_WPDMA_OFFSET1', 'EN7581_WED_WPDMA_RX_RING',
                 'EN7581_WED_BUFFER_SIZE', 'EN7581_WED_BUFS_PER_PAGE', 'EN7581_WED_PAYLOAD_SIZE'):
        definitions.append(re.search(r'^#define '+name+r'\s+[^\n]+', text, re.M)[0])
    reg_array = text[text.index('static const u32 airoha_wed_diag_registers'):text.index('static void airoha_wed_diag_ring(')]
    names = ('airoha_wed_diag_ring', 'airoha_wed_diag_buffer', 'airoha_wed_diag_snapshot',
             'airoha_wed_diag_irq', 'airoha_wed_diag_txfree')
    functions = '\n'.join(function(text, name, 'void' if name.endswith('_txfree') else 'static void') for name in names)
    irq = function(text, 'airoha_wed_irq_get', 'static u32')
    assert irq.index('airoha_wed_diag_irq(') < irq.index('airoha_wed_write('), 'capture must precede W1C'
    assert '0xbc0' not in functions and '0xc00' not in functions
    code = '\n'.join((*definitions, PREFIX, header, MIDDLE, reg_array, functions, TESTS))
    with tempfile.TemporaryDirectory(prefix='hg5585f-wed-exceptions-') as temporary:
        source, binary = Path(temporary)/'test.c', Path(temporary)/'test'
        source.write_text(code)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', str(source), '-o', str(binary)],check=True)
        subprocess.run([str(binary)],check=True)


if __name__ == '__main__':
    main()
