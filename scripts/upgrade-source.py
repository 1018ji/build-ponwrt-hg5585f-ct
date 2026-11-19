#!/usr/bin/env python3
"""Reuse a verified OpenWrt tree when the recipe appends patches or moves the source commit."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare import apply as apply_recipe, manifest, matches, run

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


def git_output(source, *arguments):
    return run('git', *arguments, cwd=source, capture=True).stdout


def git_names(source, *arguments):
    return {name for name in git_output(source, *arguments, '-z').split('\0') if name}


def switch_commit(source, previous, current):
    """Move an existing tree to a new pinned commit and keep its build products."""
    head = git_output(source, 'rev-parse', 'HEAD').strip()
    assert head == previous['source_commit'], \
        'Existing source does not match the commit recorded by its previous recipe'
    for name in previous['files']:
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
    assert matches(source, previous), \
        'Existing exported files differ from the previous recipe; refusing to overwrite changes'
    commit = current['source_commit']
    # Nothing in the tree is changed until the new commit is fetched and the
    # complete patch sequence is proven to reproduce the current manifest.
    try:
        run('git', 'fetch', '--depth=1', current['source_url'], commit, cwd=source)
    except subprocess.CalledProcessError:
        raise SystemExit(f"Cannot fetch source commit {commit} from {current['source_url']}")
    assert git_output(source, 'rev-parse', 'FETCH_HEAD').strip() == commit, 'Fetched commit differs from source.json'
    old_files = set(previous['files'])
    changed = git_names(source, 'diff', '--name-only', 'HEAD', 'FETCH_HEAD')
    local = git_names(source, 'diff', '--name-only', 'HEAD') - old_files
    assert not local & changed, f'Local edits overlap the new source commit: {sorted(local & changed)}'
    added = git_names(source, 'diff', '--name-only', '--diff-filter=A', 'HEAD', 'FETCH_HEAD')
    blocked = sorted(name for name in added if name not in old_files and (source/name).exists())
    assert not blocked, f'Untracked files collide with the new source commit: {blocked}'
    patches = [str(ROOT/'patches'/patch['name']) for patch in current['patches']]
    with tempfile.TemporaryDirectory(prefix='hg5585f-switch-check-') as temporary:
        staged = Path(temporary)
        listing = git_output(source, 'ls-tree', '-z', 'FETCH_HEAD', '--', *current['files'])
        for entry in filter(None, listing.split('\0')):
            info, name = entry.split('\t', 1)
            mode, kind, _ = info.split()
            assert kind == 'blob', f'Unexpected source entry: {name}'
            target = staged/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.run(['git', 'show', f'FETCH_HEAD:{name}'], cwd=source,
                                              check=True, capture_output=True).stdout)
            target.chmod(0o755 if mode == '100755' else 0o644)
        for patch in patches:
            run('git', 'apply', '--check', '--whitespace=nowarn', patch, cwd=staged)
            run('git', 'apply', '--whitespace=nowarn', patch, cwd=staged)
        assert matches(staged, current), 'Patch sequence differs from the export manifest on the new commit'
    tracked = []
    for name in sorted(old_files):
        known = subprocess.run(['git', 'cat-file', '-e', f'HEAD:{name}'], cwd=source,
                               stderr=subprocess.DEVNULL).returncode == 0
        if known:
            tracked.append(name)
            continue
        (source/name).unlink()
        parent = (source/name).parent
        while parent != source and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    if tracked:
        run('git', 'checkout', 'HEAD', '--', *tracked, cwd=source)
    run('git', 'checkout', '-q', '--detach', commit, cwd=source)
    print(f"Switched source from {head[:12]} to {commit[:12]} in place", flush=True)
    apply_recipe(source, current)


def upgrade(source, path, current):
    source = source.resolve()
    previous = json.loads(path.read_text())
    assert run('git', 'rev-parse', '--show-toplevel', cwd=source, capture=True).stdout.strip() == str(source)
    if git_output(source, 'rev-parse', 'HEAD').strip() != current['source_commit']:
        switch_commit(source, previous, current)
        return
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
