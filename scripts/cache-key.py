#!/usr/bin/env python3
"""Fingerprint host tools and toolchain inputs independently of driver changes."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

source = Path(sys.argv[1]).resolve()
options = dict(line.split('=', 1) for line in (source/'.config').read_text().splitlines()
               if line.startswith('CONFIG_') and '=' in line)
host = b''.join(subprocess.check_output([compiler, '--version']) for compiler in ('gcc', 'g++'))
host += subprocess.check_output(['gcc', '-dumpmachine'])
host += Path('/etc/os-release').read_bytes() if Path('/etc/os-release').exists() else b''


def digest(paths, selected, parent='', references=''):
    result = hashlib.sha256(host + parent.encode() + Path(__file__).read_bytes())
    selected = set(selected)
    listing = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others',
                                      '--exclude-standard', '--', *paths], cwd=source)
    for name in sorted(set(filter(None, os.fsdecode(listing).split('\0')))):
        path = source/name
        result.update(name.encode() + b'\0')
        if path.is_symlink():
            result.update(os.fsencode(path.readlink()) + b'\0')
        contents = path.read_bytes() if path.is_file() else b'missing'
        result.update(hashlib.sha256(contents).digest())
        if name.startswith(references) and (path.name == 'Makefile' or path.suffix == '.mk'):
            selected.update(value.decode() for value in re.findall(rb'\bCONFIG_[A-Za-z0-9_-]+', contents))
    for key in sorted(selected):
        result.update((key + '=' + options.get(key, '') + '\n').encode())
    return result.hexdigest()[:24]


common = ['include', 'scripts', 'rules.mk']
host_options = [key for key in options if key.startswith('CONFIG_HOST_')]
host_options.extend(['CONFIG_OPTIMIZE_HOST_TOOLS', 'CONFIG_AUTOREBUILD',
                     'CONFIG_AUTOREMOVE', 'CONFIG_BUILD_SUFFIX'])
tools = digest(['tools', *common], host_options, references='tools/')
families = re.compile(r'^CONFIG_(ARCH|CPU_|TARGET_(BOARD|SUBTARGET|ARCH_PACKAGES|OPTIMIZATION)|'
                      r'GCC|BINUTILS|LIBC|MUSL|GLIBC|TOOLCHAIN|EXTERNAL_|EXTRA_|USE_|SOFT_FLOAT|'
                      r'GDB|SSP_|PKG_CC_|PKG_FORTIFY_|PKG_RELRO_)')
toolchain = digest(['toolchain', *common, 'target/linux/airoha/Makefile',
                    'target/linux/airoha/an7581/target.mk', 'target/linux/generic'],
                   [key for key in options if families.match(key)], tools, references='toolchain/')
print(json.dumps({'tools': tools, 'toolchain': toolchain}))
