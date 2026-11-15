#!/usr/bin/env python3
"""Prepare the pinned PonWrt tree using only the patches exported from new."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(*arguments, cwd=None, capture=False):
    return subprocess.run(arguments, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None)


def manifest():
    data = json.loads((ROOT/'source.json').read_text())
    assert re.fullmatch(r'[0-9a-f]{40}', data['source_commit'])
    names = (ROOT/'patches/series').read_text().splitlines()
    assert names == [patch['name'] for patch in data['patches']]
    for patch in data['patches']:
        assert re.fullmatch(r'[0-9]{4}-[a-z0-9-]+\.patch', patch['name'])
        assert hashlib.sha256((ROOT/'patches'/patch['name']).read_bytes()).hexdigest() == patch['sha256']
    for name in data['files']:
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
    return data


def matches(source, data):
    for name, expected in data['files'].items():
        path = source/name
        if not path.is_file() or path.is_symlink():
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected['sha256']:
            return False
        if bool(path.stat().st_mode & 0o111) != expected['executable']:
            return False
    return True


def apply(source, data):
    source = source.resolve()
    assert source not in (ROOT, (ROOT.parent/'ponwrt').resolve()), 'Use an independent build checkout'
    assert run('git', 'rev-parse', '--show-toplevel', cwd=source, capture=True).stdout.strip() == str(source)
    assert run('git', 'rev-parse', 'HEAD', cwd=source, capture=True).stdout.strip() == data['source_commit'], 'Source commit differs from source.json'
    if not matches(source, data):
        patches = [str(ROOT/'patches'/patch['name']) for patch in data['patches']]
        # Later patches can update files introduced by earlier patches. Replay
        # the complete sequence on copies before changing the real checkout.
        with tempfile.TemporaryDirectory(prefix='hg5585f-patch-check-') as temporary:
            staged = Path(temporary)
            for name in data['files']:
                path = source/name
                if path.exists():
                    assert path.is_file() and not path.is_symlink(), f'Unexpected source file: {name}'
                    target = staged/name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
            for patch in patches:
                run('git', 'apply', '--check', '--whitespace=nowarn', patch, cwd=staged)
                run('git', 'apply', '--whitespace=nowarn', patch, cwd=staged)
            assert matches(staged, data), 'Patch sequence differs from the export manifest'
        for patch in patches:
            run('git', 'apply', '--whitespace=nowarn', patch, cwd=source)
    assert matches(source, data), 'Patched files differ from the export manifest'
    print(f"PASS: pinned source and all {len(data['files'])} exported files verified")


def config(path, kernel=False):
    values = {}
    for line in path.read_text().splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            key, value = line.split('=', 1)
            assert key not in values, f'Duplicate option: {key}'
            values[key] = value
    if kernel:
        required = ['NET_AIROHA', 'NET_AIROHA_NPU', 'NET_AIROHA_WED',
                    'NET_AIROHA_NPU_HG5585F', 'NET_AIROHA_PPE_HG5585F',
                    'NET_MEDIATEK_SOC_WED', 'DEBUG_FS']
    else:
        required = ['TARGET_airoha', 'TARGET_airoha_an7581', 'TARGET_MULTI_PROFILE',
                    'TARGET_ROOTFS_INITRAMFS', 'KERNEL_DEBUG_FS', 'PACKAGE_MAC80211_DEBUGFS',
                    'PACKAGE_kmod-mt7915e', 'PACKAGE_kmod-mt7916-firmware',
                    'PACKAGE_kmod-mac80211', 'PACKAGE_wpad-openssl',
                    'PACKAGE_luci-app-airoha-npu', 'PACKAGE_luci-theme-bootstrap',
                    'PACKAGE_airoha-en7581-npu-firmware', 'PACKAGE_kmod-nft-offload',
                    'BUSYBOX_CUSTOM', 'BUSYBOX_CONFIG_DEVMEM', 'BUSYBOX_CONFIG_TASKSET']
        devices = {key for key, value in values.items()
                   if key.startswith('CONFIG_TARGET_DEVICE_') and
                   not key.startswith('CONFIG_TARGET_DEVICE_PACKAGES_') and value == 'y'}
        expected = {'CONFIG_TARGET_DEVICE_airoha_an7581_DEVICE_fiberhome_hg5585f-ct' + suffix
                    for suffix in ('', '-wed', '-npu')}
        assert devices == expected, f'Unexpected selected devices: {devices}'
        themes = {key for key, value in values.items()
                  if key.startswith('CONFIG_PACKAGE_luci-theme-') and value == 'y'}
        assert themes == {'CONFIG_PACKAGE_luci-theme-bootstrap'}, f'Unexpected themes: {themes}'
        device_packages = ['kmod-phy-maxlinear', 'kmod-airoha-paged-bosa',
                           'kmod-airoha-xpon', 'airoha-ponctl', 'airoha-pond',
                           'luci-app-pon', 'airoha-pon-debug']
        for package in device_packages:
            assert values.get('CONFIG_PACKAGE_' + package) in ('y', 'm'), f'Missing hardware package: {package}'
    for option in required:
        assert values.get('CONFIG_' + option) == 'y', f'Missing CONFIG_{option}=y'
    print('PASS: ' + ('built kernel' if kernel else 'generated firmware') + ' configuration')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['clone', 'apply', 'config', 'kernel'])
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    if args.operation in ('config', 'kernel'):
        config(args.path, args.operation == 'kernel')
        if args.operation == 'config':
            for name, expected in manifest()['feeds'].items():
                feed = args.path.resolve().parent/'feeds'/name
                actual = run('git', 'rev-parse', 'HEAD', cwd=feed, capture=True).stdout.strip()
                assert actual == expected['commit'], f'Feed revision changed: {name}'
            print('PASS: all hardware and LuCI feed revisions pinned')
        return
    data = manifest()
    if args.operation == 'clone':
        assert not args.path.exists(), 'Clone destination already exists'
        run('git', 'init', '-q', str(args.path))
        run('git', 'remote', 'add', 'origin', data['source_url'], cwd=args.path)
        run('git', 'fetch', '--depth=1', 'origin', data['source_commit'], cwd=args.path)
        run('git', 'checkout', '--detach', 'FETCH_HEAD', cwd=args.path)
    apply(args.path, data)


if __name__ == '__main__':
    main()
