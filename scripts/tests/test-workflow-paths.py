#!/usr/bin/env python3
"""Run the workflow's source-validation commands against OpenWrt layouts."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


RECIPE = Path(__file__).resolve().parents[2]
WORKFLOW = RECIPE/'.github/workflows/hg5585f-ct-builder.yml'


def source_validation():
    text = WORKFLOW.read_text()
    start = text.index('      - name: Validate actual kernel, ROM ABI and image profiles\n')
    start = text.index('        run: |\n', start) + len('        run: |\n')
    end = text.index('          for profile in native wed npu;', start)
    return textwrap.dedent(text[start:end])


class WorkflowPaths(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hg5585f-workflow-paths-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.kernel = Path('build_dir/target-aarch64_cortex-a53_musl/'
                           'linux-airoha_an7581/linux-6.18.60')
        self.mt76 = self.kernel.parent/'mt76-2026.10.28~0936617d'
        self.calls = self.root/'calls.jsonl'
        self.write(self.kernel/'.config', 'CONFIG_NET_AIROHA_WED=y\n')
        # Stub checker internals; the real workflow selects and passes paths.
        stub = '''import json,os,sys
from pathlib import Path
with Path(os.environ['CHECK_LOG']).open('a') as log:
    log.write(json.dumps(sys.argv) + '\\n')
'''
        for name in ('check-hg5585f-ppe-accounting.py', 'check-hg5585f-rom-abi.py',
                     'check-hg5585f-wifi-diagnostics.py'):
            self.write(Path('scripts')/name, stub)
        self.write(Path('recipe/scripts/prepare.py'), stub)

    def write(self, path, contents=''):
        target = self.root/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)

    def tree(self, path, backend=True):
        self.write(path/'mt7915/pci.c')
        if backend:
            self.write(path/'npu_rx_legacy.c')

    def validate(self):
        env = dict(os.environ, GITHUB_WORKSPACE=str(self.root/'recipe'),
                   CHECK_LOG=str(self.calls))
        return subprocess.run(['bash', '-euo', 'pipefail', '-c', source_validation()],
                              cwd=self.root, env=env, text=True, capture_output=True)

    def checked_backends(self):
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        return [args[args.index('--backend') + 1] for args in calls
                if '--backend' in args]

    def test_mt76_kernel_package_layout(self):
        self.tree(self.mt76)
        # A source-like stale directory at the old path must not be selected.
        self.tree(Path('build_dir/target-aarch64_cortex-a53_musl/mt76-stale'))
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.checked_backends(), [str(self.mt76/'npu_rx_legacy.c')])
        self.assertIn(str(self.mt76), result.stdout)

    def test_other_mt76_version_and_unrelated_directory(self):
        other = self.kernel.parent/'mt76-2026-11-16-abcdef12'
        self.tree(other)
        self.write(self.kernel.parent/'mt76-tools/Makefile')
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.checked_backends(), [str(other/'npu_rx_legacy.c')])

    def test_initramfs_manifest_and_other_files_are_not_kernels(self):
        self.tree(self.mt76)
        # Kernel/SetInitramfs writes this beside LINUX_DIR, not inside it.
        self.write(Path(str(self.kernel) + '.init-cpio-list'), 'file /init init 0755 0 0\n')
        self.write(Path(str(self.kernel) + '.tar.xz'))
        self.write(Path(str(self.kernel) + '.debug'))
        self.write(self.kernel.parent/'linux-firmware-20261019/Makefile')
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.checked_backends(), [str(self.mt76/'npu_rx_legacy.c')])
        self.assertEqual(result.stdout.count('Checking built kernel source:'), 1)

    def test_unrelated_config_is_not_a_kernel(self):
        self.tree(self.mt76)
        self.write(self.kernel.parent/'linux-firmware-20261019/.config')
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.checked_backends(), [str(self.mt76/'npu_rx_legacy.c')])

    def test_multiple_configured_kernels_fail_before_any_source_check(self):
        other = self.kernel.parent/'linux-6.18.61/.config'
        self.write(other)
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Expected one configured AN7581 kernel; found 2', result.stdout)
        self.assertIn(str(self.kernel/'.config'), result.stdout)
        self.assertIn(str(other), result.stdout)
        self.assertFalse(self.calls.exists())

    def test_missing_backend_fails_with_actual_path(self):
        self.tree(self.mt76, backend=False)
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('MT76 NPU RX source missing: ' + str(self.mt76/'npu_rx_legacy.c'),
                      result.stdout)
        self.assertEqual(self.checked_backends(), [])

    def test_missing_build_directory_cannot_skip_checks(self):
        # Even if an old path exists, a missing kernel package is an error.
        self.tree(Path('build_dir/target-aarch64_cortex-a53_musl/mt76-stale'))
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('MT76 build directory missing under: ' + str(self.kernel.parent),
                      result.stdout)
        self.assertEqual(self.checked_backends(), [])

    def test_missing_kernel_cannot_skip_checks(self):
        (self.root/self.kernel/'.config').unlink()
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Expected one configured AN7581 kernel; found 0', result.stdout)
        self.assertFalse(self.calls.exists())

    def test_absent_build_tree_cannot_skip_checks(self):
        (self.root/self.kernel/'.config').unlink()
        (self.root/self.kernel).rmdir()
        (self.root/self.kernel.parent).rmdir()
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Expected one configured AN7581 kernel; found 0', result.stdout)
        self.assertFalse(self.calls.exists())


if __name__ == '__main__':
    unittest.main()
