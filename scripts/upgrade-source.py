#!/usr/bin/env python3
"""Reuse a verified OpenWrt tree when the recipe only appends patches."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare import manifest, matches, run

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = '.hg5585f-source.json'


def compatible(previous, current):
    patches = previous.get('patches', [])
    return (previous.get('source_commit') == current['source_commit'] and
            previous.get('source_url') == current['source_url'] and patches and
            patches == current['patches'][:len(patches)])


def previous_manifest(source):
    checkpoint = source / CHECKPOINT
    return checkpoint if checkpoint.is_file() else source.parent/'recipe/source.json'


def select(trees, current):
    candidates = []
    for source in trees.glob('*/openwrt'):
        if not source.is_dir():
            continue
        path = previous_manifest(source)
        try:
            previous = json.loads(path.read_text())
            if compatible(previous, current):
                candidates.append((path.stat().st_mtime_ns, str(source.parent.resolve())))
        except (OSError, ValueError):
            continue
    if candidates:
        print(max(candidates)[1])


def upgrade(source, path, current):
    source = source.resolve()
    previous = json.loads(path.read_text())
    assert run('git', 'rev-parse', '--show-toplevel', cwd=source, capture=True).stdout.strip() == str(source)
    assert run('git', 'rev-parse', 'HEAD', cwd=source, capture=True).stdout.strip() == current['source_commit'], \
        'Incremental build requires the same source commit'
    # A squash/rebase can replace the patch history while leaving the exported
    # source identical. Reuse that tree without replaying an incompatible
    # historical prefix; stale trees still go through the strict check below.
    if matches(source, current):
        print('PASS: existing source already matches the current recipe')
        return
    assert compatible(previous, current), \
        'Incremental build requires the same source commit and an unchanged prefix of patches'
    for name in previous['files']:
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
    assert matches(source, previous), \
        'Existing exported files differ from the previous recipe; refusing to overwrite changes'
    patches = current['patches'][len(previous['patches']):]
    assert patches, 'Recipe files changed without appending a patch; use a separate build tree'
    with tempfile.TemporaryDirectory(prefix='hg5585f-incremental-check-') as temporary:
        staged = Path(temporary)
        for name in set(previous['files']) | set(current['files']):
            path = source/name
            if path.exists() or path.is_symlink():
                assert path.is_file() and not path.is_symlink(), f'Unexpected source file: {name}'
                target = staged/name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        for patch in patches:
            patch_path = ROOT/'patches'/patch['name']
            run('git', 'apply', '--check', '--whitespace=nowarn', str(patch_path), cwd=staged)
            run('git', 'apply', '--whitespace=nowarn', str(patch_path), cwd=staged)
        assert matches(staged, current), 'Incremental patch result differs from the current manifest'
    for patch in patches:
        print('Applying additional patch: ' + patch['name'], flush=True)
        run('git', 'apply', '--whitespace=nowarn', str(ROOT/'patches'/patch['name']), cwd=source)
    assert matches(source, current), 'Incremental source differs from the current manifest'
    print(f'PASS: applied {len(patches)} additional patches in the original source directory')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    choose = sub.add_parser('select')
    choose.add_argument('trees', type=Path)
    apply = sub.add_parser('apply')
    apply.add_argument('source', type=Path)
    apply.add_argument('previous_manifest', type=Path)
    args = parser.parse_args()
    current = manifest()
    if args.operation == 'select':
        select(args.trees, current)
    else:
        upgrade(args.source, args.previous_manifest, current)


if __name__ == '__main__':
    main()
