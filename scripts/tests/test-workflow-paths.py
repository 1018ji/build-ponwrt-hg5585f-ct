#!/usr/bin/env python3
"""Run the workflow's source-validation commands against OpenWrt layouts."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


RECIPE = Path(__file__).resolve().parents[2]
WORKFLOW = RECIPE/'.github/workflows/hg5585f-ct-builder.yml'


def source_validation():
    text = (RECIPE/'scripts/validate-build.sh').read_text()
    return text.split('\nfor profile in native wed npu;', 1)[0]


def full_validation():
    return (RECIPE/'scripts/validate-build.sh').read_text()


def workflow_step(name):
    text = WORKFLOW.read_text()
    start = text.index('      - name: ' + name + '\n')
    end = text.find('\n      - name: ', start + 1)
    return text[start:end if end >= 0 else len(text)]


class WorkflowUploads(unittest.TestCase):
    def test_firmware_upload_follows_build_and_precedes_checks(self):
        names = re.findall(r'^      - name: (.+)$', WORKFLOW.read_text(), re.M)
        build = names.index('Build native, WED and NPU firmware')
        self.assertEqual(names[build + 1], 'Upload built firmware')
        self.assertEqual(names[build + 2], 'Validate actual kernel, ROM ABI and image profiles')
        upload = workflow_step('Upload built firmware')
        self.assertIn('uses: actions/upload-artifact@v4', upload)
        self.assertIn("steps.firmware.outcome == 'success'", upload)
        self.assertNotIn('steps.validation', upload)
        report = workflow_step('Upload validation report')
        self.assertIn("steps.validation.outcome == 'failure'", report)
        self.assertNotIn('continue-on-error', workflow_step(
            'Validate actual kernel, ROM ABI and image profiles'))

    def test_validation_failure_preserves_log_and_exit_status(self):
        step = workflow_step('Validate actual kernel, ROM ABI and image profiles')
        command = textwrap.dedent(step.split('        run: |\n', 1)[1])
        with tempfile.TemporaryDirectory(prefix='hg5585f-validation-log-') as temporary:
            root = Path(temporary)
            script = root/'scripts/validate-build.sh'
            script.parent.mkdir()
            script.write_text('echo "test validation output"\necho "test validation error" >&2\nexit 7\n')
            env = dict(os.environ, GITHUB_WORKSPACE=str(root))
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', command],
                                    cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
            self.assertEqual((root/'hg5585f-validation.log').read_text(),
                             'test validation output\ntest validation error\n')

    def test_firmware_artifact_includes_itb_and_bin_profiles(self):
        step = workflow_step('Upload built firmware')
        text = step.split('          path: |\n', 1)[1].split('          if-no-files-found:', 1)[0]
        patterns = textwrap.dedent(text).splitlines()
        with tempfile.TemporaryDirectory(prefix='hg5585f-upload-layout-') as temporary:
            root = Path(temporary)
            output = root/'openwrt/bin/targets/airoha/an7581'
            output.mkdir(parents=True)
            expected = set()
            for profile in ('ct', 'ct-wed', 'ct-npu'):
                for suffix in ('initramfs-recovery.itb', 'squashfs-sysupgrade.itb',
                               'squashfs-sysupgrade.bin'):
                    path = output/f'ponwrt-airoha-an7581-fiberhome_hg5585f-{profile}-{suffix}'
                    path.touch()
                    expected.add(path)
            unrelated = output/'ponwrt-airoha-an7581-other-device-sysupgrade.itb'
            unrelated.touch()
            uploaded = {path for pattern in patterns for path in root.glob(pattern)}
            self.assertEqual(uploaded, expected)


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
                     'check-hg5585f-wifi-diagnostics.py', 'check-hg5585f-wed-pci-irq.py',
                     'check-hg5585f-wed-rx-irq.py',
                     'check-hg5585f-images.py'):
            self.write(Path('scripts')/name, stub)
        self.write(Path('recipe/scripts/prepare.py'), stub)
        self.write(Path('recipe/scripts/tests/test-wed-probe.py'), stub)
        self.write(Path('recipe/scripts/tests/test-wed-irq-storm.py'), stub)
        self.write(Path('recipe/scripts/tests/test-npu-lifecycle.py'), stub)
        self.write(Path('scripts/validate-build.sh'), full_validation())

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

    def validate_full(self):
        env = dict(os.environ, GITHUB_WORKSPACE=str(self.root/'recipe'),
                   CHECK_LOG=str(self.calls))
        return subprocess.run(['bash', 'scripts/validate-build.sh'], cwd=self.root,
                              env=env, text=True, capture_output=True)

    def image_set(self, recovery=True, sysupgrade=True):
        output = self.root/'bin/targets/airoha/an7581'
        output.mkdir(parents=True, exist_ok=True)
        for profile in ('ct', 'ct-wed', 'ct-npu'):
            if recovery:
                (output/f'ponwrt-airoha-an7581-fiberhome_hg5585f-{profile}-initramfs-recovery.itb').touch()
            if sysupgrade:
                (output/f'ponwrt-airoha-an7581-fiberhome_hg5585f-{profile}-squashfs-sysupgrade.itb').touch()

    def checked_backends(self):
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        return [args[args.index('--backend') + 1] for args in calls
                if '--backend' in args]

    def test_full_validation_requires_recovery_and_sysupgrade_per_profile(self):
        self.tree(self.mt76)
        self.image_set(recovery=True, sysupgrade=False)
        result = self.validate_full()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing recovery/sysupgrade image pair for native', result.stdout)

    def test_full_validation_runs_all_profiles_after_image_pairs_exist(self):
        self.tree(self.mt76)
        self.image_set()
        result = self.validate_full()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        image_calls = [args for args in calls if 'check-hg5585f-images.py' in args[0]]
        self.assertEqual(len(image_calls), 6)
        self.assertEqual(sum('--wed' in args for args in image_calls), 2)
        self.assertEqual(sum('--npu' in args for args in image_calls), 2)
        self.assertEqual(sum('--wed' not in args and '--npu' not in args for args in image_calls), 2)

    def test_mt76_kernel_package_layout(self):
        self.tree(self.mt76)
        # A source-like stale directory at the old path must not be selected.
        self.tree(Path('build_dir/target-aarch64_cortex-a53_musl/mt76-stale'))
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.checked_backends(), [str(self.mt76/'npu_rx_legacy.c')])
        self.assertIn(str(self.mt76), result.stdout)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        irq_calls = [args for args in calls if 'check-hg5585f-wed-pci-irq.py' in args[0]]
        self.assertEqual(irq_calls, [['scripts/check-hg5585f-wed-pci-irq.py',
                                     '--mt76', str(self.mt76), '--kernel', str(self.kernel)]])
        rx_calls = [args for args in calls if 'check-hg5585f-wed-rx-irq.py' in args[0]]
        self.assertEqual(rx_calls, [['scripts/check-hg5585f-wed-rx-irq.py', '--mt76', str(self.mt76)]])

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

    def test_initramfs_directory_with_config_is_not_a_kernel(self):
        self.tree(self.mt76)
        self.write(self.kernel.parent/'linux-6.18.60.init-cpio-list/.config')
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
