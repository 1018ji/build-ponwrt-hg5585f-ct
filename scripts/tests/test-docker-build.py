#!/usr/bin/env python3
"""Exercise Docker build reuse and commit output handling without a compiler."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]


class DockerBuild(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hg5585f-docker-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.work = self.root/'workdir'
        self.run_root = self.root/'run'
        self.recipe = self.root/'recipe'
        self.bin = self.root/'bin'
        for path in (self.work, self.run_root, self.recipe, self.bin):
            path.mkdir()
        self.commit = '0123456789abcdef' + 'a'*24
        self.output = self.run_root/self.commit[:12]
        self.seed = self.root/'seed'
        self.seed.mkdir()
        feed = self.seed/'feeds/packages'
        feed.mkdir(parents=True)
        self.git(feed, 'init', '-q')
        self.git(feed, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
                 'commit', '--allow-empty', '-qm', 'feed')
        feed_commit = self.git(feed, 'rev-parse', 'HEAD').stdout.strip()
        self.write(self.recipe/'source.json', json.dumps({
            'feeds': {'packages': {'commit': feed_commit}},
        }))
        self.write(self.recipe/'scripts/prepare.py', """import os,shutil,sys
from pathlib import Path
if sys.argv[1] == 'clone':
    shutil.copytree(os.environ['BUILD_SEED'], sys.argv[2])
    with Path(os.environ['CLONE_LOG']).open('a') as log:
        log.write('clone\\n')
""")
        for name in ('upgrade-source.py', 'tests/test-incremental.py',
                     'tests/test-workflow-paths.py'):
            self.write(self.recipe/'scripts'/name, 'print("fixture check passed")\n')
        self.write(self.recipe/'scripts/validate-build.sh',
                   'echo validation-result\nexit "${VALIDATION_STATUS:-0}"\n')
        self.write(self.seed/'.config', 'CONFIG_CCACHE=y\n')
        self.write(self.seed/'scripts/check-hg5585f-offload.py', 'print("bundle checked")\n')
        self.executable(self.seed/'scripts/kconfig.pl', '#!/bin/bash\ncat "$2"\n')
        self.executable(self.seed/'scripts/feeds', f"""#!{sys.executable}
import os,shutil,sys
from pathlib import Path
with Path(os.environ['FEED_LOG']).open('a') as log:
    log.write(' '.join(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'update':
    dest = Path('feeds/packages')
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(Path(os.environ['BUILD_SEED'])/'feeds/packages', dest)
""")
        self.write(self.seed/'feeds/pon_userspace/luci-app-pon/Makefile', 'fixture\n')
        images = self.seed/'bin/targets/airoha/an7581'
        for name in ('ponwrt-airoha-an7581-fiberhome_hg5585f-ct-npu-squashfs-sysupgrade.itb',
                     'recovery image.itb', 'sysupgrade.itb', 'sysupgrade.bin',
                     'sha256sums', 'packages/unrelated.itb'):
            self.write(images/name, name)
        self.source = self.work/'code/openwrt'
        shutil.copytree(self.seed, self.source)
        self.write(self.source/'.hg5585f-source.json',
                   (self.recipe/'source.json').read_text())
        self.cache = self.source/'build_dir/keep.o'
        self.write(self.cache, 'keep cached object')
        self.other = self.run_root/('b'*12)/'old.itb'
        self.write(self.other, 'another commit')
        self.executable(self.bin/'make', '#!/bin/bash\necho "make $*"\n')
        self.executable(self.bin/'ccache', '#!/bin/bash\necho cache-statistics\n')
        # The build image supplies Linux sed/flock; only these tools are stubbed.
        self.executable(self.bin/'sed', '#!/bin/bash\nexit 0\n')
        self.executable(self.bin/'flock', '#!/bin/bash\nexit "${LOCK_STATUS:-0}"\n')
        text = (REPO/'docker/build.sh').read_text()
        mounts = {'workdir': self.work, 'recipe': self.recipe, 'run': self.run_root}
        text = re.sub(r'(?<![\w$])/(workdir|recipe|run)\b',
                      lambda match: str(mounts[match[1]]), text)
        self.script = self.root/'build.sh'
        self.write(self.script, text)

    @staticmethod
    def write(path, contents):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)

    def executable(self, path, contents):
        self.write(path, contents)
        path.chmod(0o755)

    @staticmethod
    def git(path, *args):
        return subprocess.run(['git', *args], cwd=path, text=True,
                              capture_output=True, check=True)

    def build(self, **extra):
        env = dict(os.environ, PATH=str(self.bin)+os.pathsep+os.environ['PATH'],
                   HG5585F_JOBS='2', HG5585F_RECIPE_COMMIT=self.commit,
                   BUILD_SEED=str(self.seed), CLONE_LOG=str(self.root/'clones'),
                   FEED_LOG=str(self.root/'feeds.log'))
        env.update(extra)
        return subprocess.run(['bash', str(self.script)], env=env, text=True,
                              capture_output=True)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_output_is_flat_and_clears_only_the_current_commit(self):
        self.write(self.output/'old/subdirectory/stale.itb', 'stale')
        self.assert_success(self.build())
        self.assertEqual({p.name for p in self.output.iterdir()},
                         {'steps.log', 'download.log', 'build.log', 'validation.log',
                          'ccache.log', 'ponwrt-airoha-an7581-fiberhome_hg5585f-squashfs-sysupgrade.itb'})
        self.assertEqual(self.other.read_text(), 'another commit')
        self.assertEqual(self.cache.read_text(), 'keep cached object')
        self.assertIn(self.commit, (self.output/'steps.log').read_text())
        self.assertNotIn('target', {p.name for p in self.run_root.iterdir()})

    def test_retry_retains_source_and_pinned_feeds(self):
        stamp = self.cache.stat().st_mtime_ns
        for _ in range(2):
            result = self.build()
            self.assert_success(result)
            self.assertIn('Reusing pinned package feeds', result.stdout)
            self.assertNotIn('START Clone pinned OpenWrt source', result.stdout)
        self.assertEqual(self.cache.stat().st_mtime_ns, stamp)
        self.assertNotIn('update', (self.root/'feeds.log').read_text())

    def test_recipe_snapshot_excludes_python_bytecode(self):
        self.write(self.recipe/'scripts/__pycache__/host.cpython-314.pyc', 'host cache')
        self.write(self.recipe/'scripts/host.pyc', 'host cache')
        self.assert_success(self.build())
        snapshot = self.work/'code/recipe'
        self.assertFalse((snapshot/'scripts/__pycache__').exists())
        self.assertFalse((snapshot/'scripts/host.pyc').exists())
        self.assertTrue((snapshot/'scripts/prepare.py').is_file())

    def test_missing_source_is_cloned_once(self):
        shutil.rmtree(self.source)
        self.assert_success(self.build())
        self.assert_success(self.build())
        self.assertEqual((self.root/'clones').read_text(), 'clone\n')

    def test_missing_feed_is_updated_then_reused(self):
        shutil.rmtree(self.source/'feeds/packages')
        self.assert_success(self.build())
        self.assert_success(self.build())
        self.assertEqual((self.root/'feeds.log').read_text().count('update -a'), 1)

    def test_failed_validation_keeps_logs_without_old_or_new_images(self):
        self.write(self.output/'old.itb', 'stale')
        result = self.build(VALIDATION_STATUS='7')
        self.assertEqual(result.returncode, 7, result.stdout+result.stderr)
        self.assertEqual(list(self.output.glob('*.itb')), [])
        self.assertIn('validation-result', (self.output/'validation.log').read_text())
        self.assertEqual(self.other.read_text(), 'another commit')

    def test_failed_copy_publishes_no_images(self):
        self.executable(self.bin/'cp', '#!/bin/bash\nexit 9\n')
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.output.glob('*.itb')), [])
        self.assertEqual(list(self.output.glob('.images.*')), [])

    def test_invalid_commit_and_busy_lock_preserve_existing_outputs(self):
        sentinel = self.output/'old.itb'
        self.write(sentinel, 'keep')
        for env in ({'HG5585F_RECIPE_COMMIT': '../..'}, {'LOCK_STATUS': '1'}):
            result = self.build(**env)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(sentinel.read_text(), 'keep')
            self.assertEqual(self.other.read_text(), 'another commit')


if __name__ == '__main__':
    unittest.main()
