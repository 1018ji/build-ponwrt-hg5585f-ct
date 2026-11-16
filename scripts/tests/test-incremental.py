#!/usr/bin/env python3
"""Check append-only patch upgrades without moving cached build paths."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

RECIPE = Path(__file__).resolve().parents[2]


class IncrementalSource(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hg5585f-incremental-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.recipe = self.root/'recipe'
        (self.recipe/'scripts').mkdir(parents=True)
        (self.recipe/'patches').mkdir()
        for name in ('prepare.py', 'upgrade-source.py'):
            shutil.copyfile(RECIPE/'scripts'/name, self.recipe/'scripts'/name)
        self.source = self.root/'trees/original-path/openwrt'
        self.source.mkdir(parents=True)
        self.git('init', '-q')
        self.export = self.source/'export.txt'
        self.export.write_text('base\n')
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'base')
        self.commit = self.git('rev-parse', 'HEAD').stdout.strip()
        first = b'--- a/export.txt\n+++ b/export.txt\n@@ -1 +1 @@\n-base\n+one\n'
        second = b'--- a/export.txt\n+++ b/export.txt\n@@ -1 +1 @@\n-one\n+two\n'
        self.patches = []
        for i, patch in enumerate((first, second), 1):
            name = f'{i:04d}-fixture.patch'
            (self.recipe/'patches'/name).write_bytes(patch)
            self.patches.append({'name': name, 'sha256': hashlib.sha256(patch).hexdigest()})
        self.git('apply', str(self.recipe/'patches/0001-fixture.patch'))
        self.old = {'source_url': 'fixture', 'source_commit': self.commit,
                    'patches': self.patches[:1],
                    'files': {'export.txt': self.file_info(b'one\n')}}
        self.current = dict(self.old, patches=self.patches,
                            files={'export.txt': self.file_info(b'two\n')})
        self.previous = self.source/'.hg5585f-source.json'
        self.previous.write_text(json.dumps(self.old))
        (self.source.parent/'recipe').mkdir()
        (self.source.parent/'recipe/source.json').write_text(json.dumps(self.old))
        self.save_current()
        self.cache = self.source/'build_dir/retained.o'
        self.cache.parent.mkdir()
        self.cache.write_bytes(b'cached compiler output')
        self.cache_stat = self.cache.stat()

    @staticmethod
    def file_info(contents):
        return {'sha256': hashlib.sha256(contents).hexdigest(), 'executable': False}

    def save_current(self):
        (self.recipe/'source.json').write_text(json.dumps(self.current))
        (self.recipe/'patches/series').write_text(''.join(p['name']+'\n' for p in self.current['patches']))

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.source, text=True,
                              capture_output=True, check=True)

    def run_helper(self, *args):
        return subprocess.run([sys.executable, str(self.recipe/'scripts/upgrade-source.py'),
                               *map(str, args)], capture_output=True, text=True)

    def upgrade(self):
        return self.run_helper('apply', self.source, self.previous)

    def assert_original_untouched(self):
        self.assertEqual(self.export.read_text(), 'one\n')
        self.assertEqual(self.cache.read_bytes(), b'cached compiler output')

    def test_appends_only_new_patch_and_retains_cache_and_absolute_path(self):
        source = self.source.resolve()
        result = self.upgrade()
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('Applying additional patch: 0002-fixture.patch', result.stdout)
        self.assertNotIn('Applying additional patch: 0001', result.stdout)
        self.assertEqual(self.export.read_text(), 'two\n')
        self.assertEqual(self.source.resolve(), source)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.strip(), self.commit)
        self.assertEqual(self.cache.stat().st_ino, self.cache_stat.st_ino)
        self.assertEqual(self.cache.stat().st_mtime_ns, self.cache_stat.st_mtime_ns)

    def test_already_upgraded_source_can_be_retried_with_old_checkpoint(self):
        self.assertEqual(self.upgrade().returncode, 0)
        result = self.upgrade()
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('already matches', result.stdout)

    def test_modified_source_is_preserved_and_rejected(self):
        self.export.write_text('local changes\n')
        result = self.upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('refusing to overwrite changes', result.stderr)
        self.assertEqual(self.export.read_text(), 'local changes\n')

    def test_changed_existing_patch_is_rejected(self):
        path = self.recipe/'patches/0001-fixture.patch'
        path.write_bytes(path.read_bytes()+b'\n')
        self.current['patches'][0] = dict(self.patches[0], sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.save_current()
        result = self.upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unchanged prefix', result.stderr)
        self.assert_original_untouched()

    def test_changed_source_revision_is_rejected(self):
        self.current['source_commit'] = 'f'*40
        self.save_current()
        result = self.upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('same source commit', result.stderr)
        self.assert_original_untouched()

    def test_manifest_mismatch_fails_before_mutating_original_source(self):
        self.current['files'] = {'export.txt': self.file_info(b'unexpected\n')}
        self.save_current()
        result = self.upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('differs from the current manifest', result.stderr)
        self.assert_original_untouched()

    def test_select_recognizes_old_recipe_and_checkpoint(self):
        result = self.run_helper('select', self.root/'trees')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.source.parent))
        self.previous.unlink()
        result = self.run_helper('select', self.root/'trees')
        self.assertEqual(result.stdout.strip(), str(self.source.parent))

    def test_select_skips_incompatible_tree(self):
        self.old['source_commit'] = 'f'*40
        self.previous.write_text(json.dumps(self.old))
        result = self.run_helper('select', self.root/'trees')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assert_original_untouched()


if __name__ == '__main__':
    unittest.main()
