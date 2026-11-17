#!/usr/bin/env python3
"""Execute the hotplug worker with delayed bridge membership and reload failures."""
import argparse
import os
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('--hotplug', type=Path, required=True)
args, tests = parser.parse_known_args()
SCRIPT = args.hotplug
sys.argv = [sys.argv[0]] + tests


class HotplugTests(unittest.TestCase):
    def run_worker(self, *, ready_at=4, reload_failures=0, present=True,
                   already=False, enabled=True, device='phy1-ap0'):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bindir = root/'bin'
            bindir.mkdir()
            (root/'locks').mkdir()
            if present:
                (root/'net'/device).mkdir(parents=True)
            for name, code in {
                'flock': 'exit 0',
                'uci': 'echo '+('1' if enabled else '0'),
                'logger': 'echo timeout >> "$TEST_ROOT/events"',
                'sleep': '''n=$(cat "$TEST_ROOT/ticks" 2>/dev/null || echo 0)
n=$((n+1))
echo "$n" > "$TEST_ROOT/ticks"
if [ "$n" -ge "$READY_AT" ] && [ -d "$TEST_ROOT/net/$DEVICENAME" ]; then
 mkdir -p "$TEST_ROOT/net/$DEVICENAME/brport"
fi''',
                'nft': '''if [ -f "$TEST_ROOT/loaded" ]; then
 echo 'devices = { "'$DEVICENAME'" };'
fi''',
                'firewall': '''n=$(cat "$TEST_ROOT/reloads" 2>/dev/null || echo 0)
n=$((n+1))
echo "$n" > "$TEST_ROOT/reloads"
[ "$n" -gt "$FAILURES" ] || exit 1
touch "$TEST_ROOT/loaded"''',
            }.items():
                p = bindir/name
                p.write_text('#!/bin/sh\n'+code+'\n')
                p.chmod(0o755)
            if already:
                (root/'loaded').touch()
            text = SCRIPT.read_text().replace('/sys/class/net', str(root/'net'))
            text = text.replace('/var/lock', str(root/'locks'))
            text = text.replace('/etc/init.d/firewall', str(bindir/'firewall'))
            text = text.replace(') </dev/null >/dev/null 2>&1 &', ')')
            worker = root/'worker'
            worker.write_text(text)
            env = dict(os.environ, PATH=str(bindir)+':'+os.environ['PATH'],
                       TEST_ROOT=str(root), DEVICENAME=device, ACTION='add',
                       READY_AT=str(ready_at), FAILURES=str(reload_failures))
            subprocess.run(['sh', str(worker)], env=env, check=True, timeout=5)
            return {name: (root/name).read_text() if (root/name).exists() else ''
                    for name in ('ticks', 'reloads', 'events')}

    def test_late_bridge_membership(self):
        state = self.run_worker(ready_at=4)
        self.assertEqual(state['reloads'], '1\n')
        self.assertGreaterEqual(int(state['ticks']), 5)

    def test_reload_failure_is_retried(self):
        self.assertEqual(self.run_worker(reload_failures=1)['reloads'], '2\n')

    def test_existing_tables_need_no_reload(self):
        self.assertEqual(self.run_worker(already=True)['reloads'], '')

    def test_disappearing_interface_stops_worker(self):
        state = self.run_worker(present=False)
        self.assertEqual(state['ticks'], '1\n')
        self.assertEqual(state['reloads'], '')

    def test_disabled_offload(self):
        self.assertEqual(self.run_worker(enabled=False)['reloads'], '')

    def test_missing_bridge_wait_is_bounded(self):
        state = self.run_worker(ready_at=100)
        self.assertEqual(state['ticks'], '30\n')
        self.assertEqual(state['reloads'], '')
        self.assertEqual(state['events'], 'timeout\n')

    def test_non_ap_event_is_ignored(self):
        self.assertEqual(self.run_worker(device='lan3')['ticks'], '')


if __name__ == '__main__':
    unittest.main()
