"""Supervisor and validator fault controls. No GPUI, npm, or native execution."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('readme_runner', Path(__file__).with_name('run.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class Supervision(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def command(self, source, timeout=2):
        return runner.command([sys.executable, '-c', source], self.out, os.environ.copy(), self.out, 'child', timeout, grace=.05)

    def row(self):
        return json.loads((self.out / 'child.json').read_text())

    def test_success_and_original_failure(self):
        self.assertEqual(self.command("print('real child output')").strip(), 'real child output')
        self.assertEqual(self.row()['status'], 'passed')
        self.assertEqual(self.row()['ownedProcesses'][0]['pid'], self.row()['pid'])
        with self.assertRaises(RuntimeError):
            self.command('raise SystemExit(17)')
        self.assertEqual(self.row()['status'], 'command-failed')
        self.assertEqual(self.row()['exit'], 17)
        self.assertEqual(self.row()['survivors'], [])

    def test_spawn_failure_is_not_a_pass_and_has_evidence(self):
        with self.assertRaises(FileNotFoundError):
            runner.command(['/not/a/real/executable'], self.out, {}, self.out, 'child', 1)
        self.assertEqual(self.row()['status'], 'spawn-failed')
        self.assertNotIn('exit', self.row())
        self.assertTrue((self.out / 'child.log').exists())

    def test_timeout_is_not_hidden_by_cleanup(self):
        with self.assertRaises(RuntimeError):
            self.command('import time; time.sleep(60)', .1)
        self.assertEqual(self.row()['status'], 'timed-out')
        self.assertNotEqual(self.row()['exit'], 0)
        self.assertEqual(self.row()['survivors'], [])

    def test_interruption_preserves_failure_and_cleans_owned_child(self):
        original = runner.members
        first = True
        def interrupt_once(pgid):
            nonlocal first
            if first:
                first = False
                raise InterruptedError('mock supervision signal')
            return original(pgid)
        with patch.object(runner, 'members', side_effect=interrupt_once):
            with self.assertRaises(InterruptedError):
                self.command('import time; time.sleep(60)')
        self.assertEqual(self.row()['status'], 'interrupted')
        self.assertEqual(self.row()['survivors'], [])

    def test_leaked_descendant_is_failed_and_owned_cleanup_finishes(self):
        with self.assertRaises(RuntimeError):
            self.command("import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])")
        self.assertEqual(self.row()['status'], 'leaked-children')
        self.assertEqual(self.row()['exit'], 0)
        self.assertGreater(len(self.row()['leaked']), 0)
        self.assertEqual(self.row()['survivors'], [])

    def test_early_preflight_preserves_original_error_and_manifest(self):
        with patch.object(runner.platform, 'system', return_value='Linux'):
            with self.assertRaisesRegex(RuntimeError, 'requires real macOS'):
                runner.run(self.out / 'missing-target', self.out / 'evidence')
        result = json.loads((self.out / 'evidence/result.json').read_text())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['phase'], 'preflight')
        self.assertIn('requires real macOS', result['error'])
        self.assertIn('sourceError', result)
        self.assertTrue((self.out / 'evidence/manifest.json').exists())


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)
        box = {'x': 24, 'y': 64, 'width': 100, 'height': 24}
        window = {'width': 800, 'height': 600}
        self.rows = [
            {'kind': 'startup', 'pid': 123, 'native': True, 'platform': 'darwin', 'arch': 'arm64', 'bun': '1.3.9'},
            {'kind': 'before', 'pid': 123, 'count': 0, 'containerConnected': False, 'buttonConnected': True, 'children': 1, 'frames': 4, 'text': ['Count: 0', '+1'], 'painted': ['Count: 0', '+1'], 'tree': {'id': 1}, 'id': 2, 'bounds': box, 'window': window, 'screenshot': {'width': 800, 'height': 600, 'varied': 500}},
            {'kind': 'input', 'pid': 123, 'method': 'simulateClick', 'id': 2, 'bounds': box, 'window': window, 'point': {'x': 74, 'y': 76}},
            {'kind': 'after', 'pid': 123, 'count': 1, 'containerConnected': False, 'buttonConnected': True, 'children': 1, 'frames': 8, 'text': ['Count: 1', '+1'], 'painted': ['Count: 1', '+1'], 'tree': {'id': 1}, 'screenshot': {'width': 800, 'height': 600, 'varied': 500}},
            {'kind': 'close-request', 'pid': 123, 'reason': 'close', 'beforeClose': 1, 'containerConnected': False, 'buttonConnected': True},
            {'kind': 'exit', 'pid': 123, 'code': 0, 'closeRequests': 1, 'beforeClose': 1, 'children': 0, 'containerRestored': True, 'buttonDisconnected': True, 'disposalError': None, 'hostDetached': True, 'globalsRestored': True, 'stateDetached': True},
        ]
        self.child = {'status': 'passed', 'exit': 0, 'pid': 123, 'leaked': [], 'survivors': [], 'cleanupError': None}
        # Mock header only: validator controls, never presented as a real screenshot.
        for count in [0, 1]:
            (self.out / f'count-{count}.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'\0' * 8 + struct.pack('>II', 800, 600) + b'\0' * 120)

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, rows=None, child=None):
        runner.save(self.out, 'observations', self.rows if rows is None else rows)
        runner.save(self.out, 'native-readme', self.child if child is None else child)
        runner.validate(self.out)

    def test_positive_mock_contract(self):
        self.validate()

    def test_missing_stage_skipped_input_or_fake_host_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.validate(self.rows[1:])
        for index, field, value in [(0, 'native', False), (0, 'platform', 'linux'), (0, 'bun', '1.4.2'), (2, 'method', 'dispatchEvent'), (2, 'id', 99)]:
            rows = copy.deepcopy(self.rows)
            rows[index][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RuntimeError):
                self.validate(rows)

    def test_vacuous_paint_stale_geometry_and_no_model_advancement_rejected(self):
        for index, field, value in [(1, 'painted', []), (1, 'children', 0), (1, 'tree', None), (3, 'frames', 4), (3, 'text', ['Count: 0']), (2, 'point', {'x': 900, 'y': 76})]:
            rows = copy.deepcopy(self.rows)
            rows[index][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.validate(rows)
        (self.out / 'count-0.png').unlink()
        with self.assertRaises(FileNotFoundError):
            self.validate()

    def test_missing_runtime_ownership_or_teardown_and_failed_exit_rejected(self):
        for field, value in [('children', 1), ('containerRestored', False), ('buttonDisconnected', False), ('disposalError', 'not disposed'), ('beforeClose', 0), ('code', 1), ('closeRequests', 0), ('hostDetached', False), ('globalsRestored', False), ('stateDetached', False)]:
            rows = copy.deepcopy(self.rows)
            rows[-1][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.validate(rows)
        for field, value in [('status', 'timed-out'), ('exit', 1), ('pid', 124), ('leaked', [{'pid': 125}]), ('survivors', [{'pid': 125}]), ('cleanupError', 'failed')]:
            child = copy.deepcopy(self.child)
            child[field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.validate(child=child)

    def test_source_drift_is_rejected(self):
        with patch.object(runner.subprocess, 'check_output', side_effect=[runner.HEAD, '']), patch.object(Path, 'read_bytes', return_value=b'drift'):
            with self.assertRaisesRegex(RuntimeError, 'source absent'):
                runner.source(self.out)


if __name__ == '__main__':
    unittest.main()
