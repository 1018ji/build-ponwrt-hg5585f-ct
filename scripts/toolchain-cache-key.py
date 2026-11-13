#!/usr/bin/env python3
"""Hash effective toolchain source and relevant generated OpenWrt configuration."""
from pathlib import Path
import argparse,hashlib,re,subprocess

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('source',type=Path,nargs='?',default=Path.cwd())
args=parser.parse_args()
source=args.source.resolve()
config=source/'.config'
if not config.is_file():parser.error('generated .config is missing; run make defconfig first')
values={}
for line in config.read_text().splitlines():
 if line.startswith('CONFIG_') and '=' in line:
  key,value=line.split('=',1);values[key]=value
board=values.get('CONFIG_TARGET_BOARD','').strip('"')
subtarget=values.get('CONFIG_TARGET_SUBTARGET','').strip('"')
for name,value in [('board',board),('subtarget',subtarget)]:
 if not re.fullmatch(r'[A-Za-z0-9_-]+',value):parser.error('missing or invalid target '+name)
paths=['tools','toolchain','include','config','rules.mk','Config.in',f'target/linux/{board}/Makefile',f'target/linux/{board}/{subtarget}/target.mk']
for path in paths:
 if not (source/path).exists():parser.error('required toolchain source path missing: '+path)
# Read current working files, including added patch inputs; HEAD hashes alone miss edits.
result=subprocess.run(['git','-C',str(source),'ls-files','-z','--cached','--others','--exclude-standard','--']+paths,check=True,capture_output=True)
files=sorted(set(filter(None,result.stdout.decode().split('\0'))))
digest=hashlib.sha256()
def add(name,data):
 digest.update(name.encode()+b'\0'+str(len(data)).encode()+b'\0'+data+b'\0')
add('algorithm',Path(__file__).read_bytes())
for name in files:
 path=source/name
 if path.is_symlink():add('symlink:'+name,str(path.readlink()).encode())
 elif path.is_file():add('file:'+name,path.read_bytes())
 else:add('missing:'+name,b'')
relevant=re.compile(r'^CONFIG_(ARCH(?:$|_)|HOST_OS_|TARGET_(?:BOARD$|SUBTARGET$|ARCH_PACKAGES$|OPTIMIZATION$)|CPU_TYPE$|EXTRA_OPTIMIZATION$|SOFT_FLOAT$|GCC_|BINUTILS_|LIBC|MUSL|GLIBC|TOOLCHAIN|EXTERNAL_TOOLCHAIN|USE_|CCACHE|DEVEL$|INSTALL_|GDB|SSP_|BUILD_NLS$|KERNEL_)')
for key,value in sorted(values.items()):
 if relevant.match(key):add('config:'+key,value.encode())
print(digest.hexdigest()[:24])
