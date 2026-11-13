#!/usr/bin/env python3
"""Verify CT FIT images boot the combined offload DTB without extra overlays."""
from pathlib import Path
import argparse,struct

class Fdt:
    def __init__(self,data):
        if len(data)<40:raise ValueError('truncated FDT header')
        magic,total,off_struct,off_strings,_,version,_,_,strings_size,struct_size=struct.unpack_from('>10I',data)
        if magic!=0xd00dfeed or version<17 or total>len(data) or off_strings+strings_size>total or off_struct+struct_size>total:raise ValueError('invalid FDT header')
        self.total=total;self.nodes={};stack=[];pos=off_struct;end=off_struct+struct_size
        while pos<end:
            token=struct.unpack_from('>I',data,pos)[0];pos+=4
            if token==1:
                stop=data.index(b'\0',pos,end);stack.append(data[pos:stop].decode('ascii'));pos=(stop+4)&~3
                self.nodes['/'.join(stack) or '/']={}
            elif token==2:
                if not stack:raise ValueError('unbalanced FDT nodes')
                stack.pop()
            elif token==3:
                length,offset=struct.unpack_from('>2I',data,pos);pos+=8
                if not stack or pos+length>end or offset>=strings_size:raise ValueError('invalid FDT property')
                name=data[off_strings+offset:data.index(b'\0',off_strings+offset,off_strings+strings_size)].decode('ascii')
                node=self.nodes['/'.join(stack) or '/']
                if name in node:raise ValueError('duplicate FDT property')
                node[name]=data[pos:pos+length];pos=(pos+length+3)&~3
            elif token==4:continue
            elif token==9:
                if stack:raise ValueError('unbalanced FDT end')
                return
            else:raise ValueError('invalid FDT token')
        raise ValueError('missing FDT end')
    def prop(self,node,name):
        try:return self.nodes[node][name]
        except KeyError:raise ValueError(f'missing {node}:{name}') from None
    def string(self,node,name):
        v=self.prop(node,name)
        if not v.endswith(b'\0') or b'\0' in v[:-1]:raise ValueError(f'expected one string: {node}:{name}')
        return v[:-1].decode('ascii')
    def cells(self,node,name):
        v=self.prop(node,name)
        if len(v)%4:raise ValueError('unaligned cells')
        return struct.unpack('>'+str(len(v)//4)+'I',v)

NPU='/soc/npu@1e900000'
WIFI='/soc/pcie@1fc20000/pcie@0,0/wifi@0,0'
WED='/soc/wed@1fa03000'
ETH='/soc/ethernet@1fb50000'

def verify_dtb(data):
    tree=Fdt(data)
    if tree.string('/','model')!='FiberHome HG5585F CT':raise ValueError('wrong board model')
    for node in [NPU,WIFI,WED,ETH]:
        parent=node
        while parent:
            if parent not in tree.nodes:raise ValueError('missing enabled node: '+parent)
            if 'status' in tree.nodes[parent] and tree.string(parent,'status') not in ['okay','ok']:raise ValueError('disabled hardware path: '+parent)
            parent=parent.rsplit('/',1)[0]
    for node,key in [(WIFI,'airoha,hg5585f-rx-offload'),(ETH,'airoha,hg5585f-ppe')]:
        if tree.prop(node,key)!=b'':raise ValueError('expected boolean property')
    if tree.string(NPU,'airoha,wlan-firmware-abi')!='hg5585f-2024':raise ValueError('wrong NPU firmware ABI')
    if tree.prop(NPU,'firmware-name')!=b'airoha/hg5585f_npu_rv32.bin\0airoha/hg5585f_npu_data.bin\0':raise ValueError('wrong firmware pair')
    if tree.prop(NPU,'memory-region-names')!=b'binary\0pkt\0ba\0':raise ValueError('wrong RX reservations')
    if tree.string(WED,'compatible')!='airoha,en7581-wed':raise ValueError('wrong WED provider')
    if tree.cells(NPU,'reg')!=(0,0x1e900000,0,0x313000,0,0x1fb30000,0,0x2b0):raise ValueError('wrong NPU/GDMA mappings')
    if tree.cells(WED,'reg')!=(0,0x1fa03000,0,0xb00,0,0x1fa06400,0,0x400):raise ValueError('wrong WED/WDMA mappings')
    resets=tree.cells(NPU,'resets')
    if len(resets)!=4 or resets[0]!=resets[2] or resets[1]!=8 or resets[3]!=38:raise ValueError('wrong exclusive NPU/GDMA resets')
    if tree.prop(NPU,'reset-names')!=b'npu\0rx-copy\0':raise ValueError('wrong reset names')
    for node,key,target in [(WIFI,'airoha,npu',NPU),(WIFI,'airoha,wed',WED),(WIFI,'airoha,eth',ETH)]:
        if tree.cells(node,key)!=tree.cells(target,'phandle'):raise ValueError('wrong provider phandle')
    memory=tree.cells(NPU,'memory-region')
    for index,node,addr,size in [(1,'/reserved-memory/npu-rx-pkt@98000000',0x98000000,0x600000),(2,'/reserved-memory/npu-rx-ba@98600000',0x98600000,0x80000)]:
        if tree.cells(node,'reg')!=(0,addr,0,size) or tree.prop(node,'no-map')!=b'':raise ValueError('wrong reserved memory')
        if len(memory)!=3 or memory[index]!=tree.cells(node,'phandle')[0]:raise ValueError('wrong reserved memory phandle')
    return tree

def verify_fit(path):
    with path.open('rb') as stream:
        header=stream.read(40)
        if len(header)<40:raise ValueError('truncated FIT header')
        total=struct.unpack_from('>I',header,4)[0]
        if total<40 or total>16*1024*1024:raise ValueError('invalid FIT metadata size')
        stream.seek(0);fit=Fdt(stream.read(total))
        default=fit.string('/configurations','default')
        if '/' in default:raise ValueError('invalid default configuration name')
        conf='/configurations/'+default
        image_name=fit.string(conf,'fdt')
        if '/' in image_name:raise ValueError('invalid default FDT image name')
        fit.string(conf,'kernel')
        image='/images/'+image_name
        if fit.string(image,'type')!='flat_dt' or fit.string(image,'compression')!='none':raise ValueError('expected uncompressed default FDT')
        props=fit.nodes[image]
        if 'data' in props:payload=props['data']
        else:
            size_cells=fit.cells(image,'data-size')
            if len(size_cells)!=1 or not 0<size_cells[0]<=2*1024*1024:raise ValueError('invalid external FDT size')
            size=size_cells[0]
            if 'data-position' in props:offset=fit.cells(image,'data-position')
            else:offset=fit.cells(image,'data-offset')
            if len(offset)!=1:raise ValueError('invalid external FDT offset')
            offset=offset[0]+(0 if 'data-position' in props else (total+3)&~3)
            if offset<total or offset+size>path.stat().st_size:raise ValueError('external FDT outside image')
            stream.seek(offset);payload=stream.read(size)
        verify_dtb(payload)
    print(f'PASS: {path.name}: default {default} directly boots CT NPU RX + host PPE + WED TX')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images',type=Path,nargs='+')
    args=parser.parse_args()
    for image in args.images:
        try:verify_fit(image)
        except (OSError,ValueError,struct.error,UnicodeError) as exc:parser.exit(1,f'FIT check failed: {image}: {exc}\n')
if __name__=='__main__':main()
