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


CHECKPOINT = '.hg5585f-source.json'


def manifest():
    data = json.loads((ROOT/'source.json').read_text())
    assert re.fullmatch(r'[0-9a-f]{40}', data['source_commit'])
    names = (ROOT/'patches/series').read_text().splitlines()
    assert names == [patch['name'] for patch in data['patches']]
    for patch in data['patches']:
        assert re.fullmatch(r'[0-9]{4}-[a-z0-9-]+\.patch', patch['name'])
        assert hashlib.sha256((ROOT/'patches'/patch['name']).read_bytes()).hexdigest() == patch['sha256']
    return data


def patch_paths(data):
    return [str(ROOT/'patches'/patch['name']) for patch in data['patches']]


def patch_files(patches):
    """Paths touched by the given patch files, in first-touched order."""
    names = []
    for patch in patches:
        parts = run('git', 'apply', '--numstat', '-z', patch, capture=True).stdout.split('\0')
        i = 0
        while i < len(parts) and parts[i]:
            path = parts[i].split('\t', 2)[2]
            if path:
                names.append(path)
                i += 1
            else:  # rename: the old and new paths follow as separate fields
                names.extend(parts[i + 1:i + 3])
                i += 3
    for name in names:
        assert not Path(name).is_absolute() and '..' not in Path(name).parts, f'Unsafe patch path: {name}'
    return list(dict.fromkeys(names))


def fingerprint(source, names):
    files = {}
    for name in sorted(names):
        path = source/name
        if path.is_file() and not path.is_symlink():
            files[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'executable': bool(path.stat().st_mode & 0o111)}
    return files


def matches(source, recorded):
    """Compare patched files with the fingerprints recorded when they were applied."""
    for name, expected in recorded['files'].items():
        path = source/name
        if not path.is_file() or path.is_symlink():
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected['sha256']:
            return False
        if bool(path.stat().st_mode & 0o111) != expected['executable']:
            return False
    return True


def same_recipe(previous, data):
    return all(previous.get(key) == data[key] for key in ('source_url', 'source_commit', 'patches'))


def save_checkpoint(source, data, names):
    """Record the applied recipe plus fingerprints of the files it produced."""
    record = dict(data, files=fingerprint(source, names))
    (source/CHECKPOINT).write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    return record


def stage(source, names, target):
    for name in names:
        path = source/name
        if path.exists() or path.is_symlink():
            assert path.is_file() and not path.is_symlink(), f'Unexpected source file: {name}'
            copy = target/name
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copy)


def apply(source, data):
    source = source.resolve()
    assert source not in (ROOT, (ROOT.parent/'ponwrt').resolve()), 'Use an independent build checkout'
    assert run('git', 'rev-parse', '--show-toplevel', cwd=source, capture=True).stdout.strip() == str(source)
    assert run('git', 'rev-parse', 'HEAD', cwd=source, capture=True).stdout.strip() == data['source_commit'], 'Source commit differs from source.json'
    checkpoint = source/CHECKPOINT
    if checkpoint.is_file():
        previous = json.loads(checkpoint.read_text())
        if same_recipe(previous, data) and 'files' in previous and matches(source, previous):
            print(f"PASS: pinned source already carries all {len(previous['files'])} patched files")
            return
    patches = patch_paths(data)
    names = patch_files(patches)
    # Later patches can update files introduced by earlier patches. Replay
    # the complete sequence on copies before changing the real checkout.
    with tempfile.TemporaryDirectory(prefix='hg5585f-patch-check-') as temporary:
        staged = Path(temporary)
        stage(source, names, staged)
        for patch in patches:
            run('git', 'apply', '--check', '--whitespace=nowarn', patch, cwd=staged)
            run('git', 'apply', '--whitespace=nowarn', patch, cwd=staged)
    for patch in patches:
        run('git', 'apply', '--whitespace=nowarn', patch, cwd=source)
    record = save_checkpoint(source, data, names)
    print(f"PASS: pinned source patched; {len(record['files'])} files recorded")


def config(path, kernel=False):
    values = {}
    for line in path.read_text().splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            key, value = line.split('=', 1)
            assert key not in values, f'Duplicate option: {key}'
            values[key] = value
        elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
            key = line[2:-len(' is not set')]
            assert key not in values, f'Duplicate option: {key}'
            values[key] = 'n'
    if kernel:
        required = ['NET_AIROHA', 'NET_AIROHA_NPU', 'NET_AIROHA_WED',
                    'NET_AIROHA_NPU_HG5585F', 'NET_AIROHA_PPE_HG5585F',
                    'NET_MEDIATEK_SOC_WED', 'DEBUG_FS']
    else:
        required = ['TARGET_airoha', 'TARGET_airoha_an7581', 'TARGET_MULTI_PROFILE',
                    'TARGET_ROOTFS_INITRAMFS', 'KERNEL_DEBUG_FS', 'PACKAGE_MAC80211_DEBUGFS',
                    'PACKAGE_kmod-mt7915e', 'PACKAGE_kmod-mt7916-firmware',
                    'PACKAGE_kmod-mac80211', 'PACKAGE_wpad-openssl',
                    'PACKAGE_luci-theme-bootstrap',
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
    if kernel:
        mediatek_soc = values.get('CONFIG_NET_MEDIATEK_SOC')
        assert mediatek_soc not in ('y', 'm'), \
            'CONFIG_NET_MEDIATEK_SOC must not be enabled for Airoha WED-only builds'
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
