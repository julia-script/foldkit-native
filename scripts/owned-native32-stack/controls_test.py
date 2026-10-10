"""Audited synthetic receipts and harmless owned Python children; no native load."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import measure as m
import prepare as safe

UUID = '11111111-2222-3333-4444-555555555555'
BINARY = {'path': '/synthetic/gpuix-native.darwin-arm64.node', 'uuid': UUID, 'sha256': m.NATIVE_SHA}


def row(pid=99991):
    return {'pid': pid, 'ppid': os.getpid(), 'pgid': pid, 'start': 'synthetic-start',
            'rssKB': '1', 'cpuTime': '0:00', 'state': 'S', 'command': '/synthetic/bun'}


def report(pid=99991):
    date = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f %z')
    return f'Process: bun [{pid}]\nDate/Time: {date}\nCall graph:\n    5 build_element (in gpuix-native.darwin-arm64.node)\nBinary Images:\n0x1000 - 0x2000 gpuix-native.darwin-arm64.node <{UUID}> {BINARY["path"]}\n'


def receipts():
    original = {'identity': row(), 'rawExit': 0, 'cleanupVerified': True}
    sample = {'rawExit': 0, 'cleanupVerified': True, 'targetIdentity': m.token(row()),
              'argv': ['/usr/bin/sample', '99991', '25', '10', '-mayDie', '-file', '/synthetic/sample.txt'],
              'start': m.stamp(), 'end': m.stamp()}
    return original, sample


class Controls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name) / 'evidence'

    def tearDown(self):
        self.temp.cleanup()

    def test_safe_plan_audits_all_before_first_child_and_rejects_extra_unsafe_step(self):
        expected = [sys.executable, '-m', 'unittest', 'discover', '-s', str(m.HERE), '-p', 'controls_test.py', '-v']
        for bad in ([['bun', 'test']], [expected, ['bun', 'test', './test/keyboard.test.ts']],
                    [expected[:-2]], [expected[:-3] + ['-p', '*test.py', '-v']], [],
                    [['/usr/bin/sample', 'arbitrary-process-name']], [['sudo', 'sample', '1']]):
            with self.subTest(plan=bad), patch.object(m, 'source_identity') as source, patch.object(m, 'Owned') as spawn:
                with self.assertRaisesRegex(RuntimeError, 'unsafe'):
                    safe.audit(bad, 'ignored')
                source.assert_not_called()
                spawn.assert_not_called()

    def test_safe_harmless_argv_source_allowlist_rejects_native_and_modified_program(self):
        for argv in (['bun', 'test'], [sys.executable, '-c', 'import ctypes; ctypes.CDLL("gpuix.node")'],
                     [sys.executable, '-c', safe.SAFE_PROGRAMS['success'], 'extra'],
                     [sys.executable, '-m', 'unittest', 'test_keyboard']):
            with self.assertRaisesRegex(RuntimeError, 'non-allowlisted'):
                safe.harmless(argv)
        self.assertEqual(safe.harmless([sys.executable, '-c', safe.SAFE_PROGRAMS['success']])['classification'], 'harmless Python, not native evidence')

    def test_preflight_failure_and_exclusive_output_spawn_zero_children(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}), patch.object(m, 'Owned') as spawn:
            self.assertEqual(m.run(self.out), 1)
            spawn.assert_not_called()
        result = json.loads((self.out / 'result.json').read_text())
        self.assertIn('local native execution forbidden', result['error'])
        self.assertIsNone(result['originalRawExit'])
        snapshot = {p.name: p.read_bytes() for p in self.out.iterdir()}
        with self.assertRaises(FileExistsError):
            m.run(self.out)
        self.assertEqual(snapshot, {p.name: p.read_bytes() for p in self.out.iterdir()})

    def test_wrong_marker_missing_marker_and_existing_sample_rejected(self):
        self.assertIsNone(m.marker('nothing ready'))
        self.assertEqual(m.marker('native test window: {"name":"pixel-art","actual":{"width":760,"height":653}}')['name'], 'pixel-art')
        for raw in ('native test window: {"name":"other","actual":{"width":760,"height":653}}',
                    'native test window: {"name":"pixel-art","actual":{"width":0,"height":0}}',
                    'native test window: {}\nnative test window: {}'):
            with self.assertRaises((RuntimeError, KeyError)):
                m.marker(raw)
        self.out.mkdir()
        (self.out / 'sample.txt').write_text('preserved')
        target = type('Target', (), {'child': type('Child', (), {'pid': 99991})(), 'verified_live': lambda self: row()})()
        with self.assertRaisesRegex(RuntimeError, 'collision'):
            m.sample_command(target, self.out)
        self.assertEqual((self.out / 'sample.txt').read_text(), 'preserved')

    def test_only_owned_live_bun_pid_and_fixed_sampler_argv(self):
        target = type('Target', (), {'child': type('Child', (), {'pid': 99991})(), 'verified_live': lambda self: row()})()
        self.out.mkdir()
        self.assertEqual(m.sample_command(target, self.out), ['/usr/bin/sample', '99991', '25', '10', '-mayDie', '-file', str(self.out / 'sample.txt')])
        for value in ({**row(), 'pid': 8}, {**row(), 'pgid': 8}, {**row(), 'command': '/usr/bin/python'}):
            with patch.object(target, 'verified_live', return_value=value), self.assertRaises(RuntimeError):
                m.sample_command(target, self.out)
        with patch.object(target, 'verified_live', side_effect=RuntimeError('stale PID')), self.assertRaisesRegex(RuntimeError, 'stale'):
            m.sample_command(target, self.out)

    def test_report_identity_symbol_linkage_and_occupancy_limits(self):
        original, sample = receipts()
        # This is a synthetic parser positive only; not a physical sample receipt.
        value = m.validate_report(report(), original, sample, BINARY)
        self.assertEqual(value['reportPID'], 99991)
        self.assertTrue(value['symbolAttributionAvailable'])
        self.assertIn('not exclusive CPU', value['interpretation'])
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            m.validate_report(report(), original, sample, BINARY, synthetic=True)

    def test_wrong_stale_report_pid_loaded_uuid_path_hash_stack_and_interval_reject(self):
        original, sample = receipts()
        malformed = [report(123), report().replace(UUID, 'A' * 36),
                     report().replace(BINARY['path'], '/unrelated/binary.node'),
                     report().replace('Call graph:', 'No graph:'), report().replace('Binary Images:', 'Missing images:'),
                     report().replace('    5 build_element (in gpuix-native.darwin-arm64.node)\n', ''),
                     report().replace('Date/Time:', 'No date:')]
        for raw in malformed:
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                m.validate_report(raw, original, sample, BINARY)
        for key, val in [('rawExit', 13), ('cleanupVerified', False), ('targetIdentity', {**m.token(row()), 'start': 'older'}),
                         ('argv', ['/usr/bin/sample', '123', '25', '10', '-mayDie', '-file', '/tmp/report']),
                         ('start', {'utc': '2099-01-01T00:00:00+00:00'})]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                m.validate_report(report(), original, {**sample, key: val}, BINARY)
        with self.assertRaises(RuntimeError):
            m.validate_report(report(), original, sample, {**BINARY, 'sha256': '0' * 64})

    def test_original_result_preserves_fail_timeout_and_exact_selection(self):
        passed = '(pass) Metal, offscreen > ' + m.TITLE + '\n33 expect() calls\n'
        self.assertEqual(m.test_verdict(passed, {'rawExit': 0}), 'pass')
        self.assertEqual(m.test_verdict(passed.replace('(pass)', '(fail)'), {'rawExit': 17}), 'fail')
        self.assertEqual(m.test_verdict(passed.replace('(pass)', '(fail)'), {'rawExit': -15}), 'fail')
        for raw in (passed.replace('33 expect()', '32 expect()'), passed + '(pass) keyboard\n', passed.replace('(pass)', '(skip)'), '0 pass'):
            with self.assertRaises(RuntimeError):
                m.test_verdict(raw, {'rawExit': 0})

    def spawn_harmless(self, program, role):
        self.out.mkdir(exist_ok=True)
        argv = [sys.executable, '-c', safe.SAFE_PROGRAMS[program]]
        safe.harmless(argv)  # Explicit source/argv allowlist before each child.
        return m.Owned(argv, self.out, role, {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})

    def test_real_harmless_natural_zero_and_raw_failure_owned_cleanup(self):
        for program, expected in [('success', 0), ('failure', 17)]:
            child = self.spawn_harmless(program, program)
            child.child.wait(timeout=5)
            receipt = child.close()
            self.assertEqual(receipt['rawExit'], expected)
            self.assertTrue(receipt['cleanupVerified'], receipt)
            self.assertFalse(receipt['survivors'])

    def test_real_harmless_timeout_and_known_descendant_cleanup(self):
        for program in ('hang', 'descendant'):
            child = self.spawn_harmless(program, program)
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    child.child.wait(timeout=.15)
                child.observe()
            finally:
                receipt = child.close()
            self.assertEqual(receipt['rawExit'], -15)
            self.assertTrue(receipt['cleanupVerified'], receipt)
            self.assertFalse(receipt['survivors'])
            self.assertIn('SIGTERM', receipt['signals'])

    def test_spawn_failure_has_immutable_early_receipt_and_no_fabricated_exit(self):
        self.out.mkdir()
        with self.assertRaises(OSError):
            m.Owned(['never-executed'], self.out, 'original', os.environ.copy(), factory=lambda *a, **k: (_ for _ in ()).throw(OSError('synthetic spawn failure')))
        receipt = json.loads((self.out / 'original-process.json').read_text())
        self.assertIsNone(receipt['rawExit'])
        self.assertFalse(receipt['cleanupVerified'])
        self.assertIn('spawn failure', receipt['spawnOrIdentityError'])

    def test_unknown_cleanup_member_fails_honestly_without_signal(self):
        obj = object.__new__(m.Owned)
        obj.child = type('Child', (), {'pid': 99991})()
        obj.known = {99991: m.token(row())}
        obj.receipt = {'signals': []}
        with patch.object(obj, 'observe', return_value={123: {**row(123), 'pgid': 99991}}), patch.object(os, 'killpg') as signal:
            with self.assertRaisesRegex(RuntimeError, 'unverified'):
                obj.send(15)
            signal.assert_not_called()

    def test_stale_live_identity_is_rejected_before_sample(self):
        obj = object.__new__(m.Owned)
        obj.child = type('Child', (), {'pid': 99991, 'poll': lambda self: None})()
        obj.known = {}
        obj.receipt = {'identity': row()}
        with patch.object(m, 'table', return_value={99991: {**row(), 'start': 'different-start'}}):
            with self.assertRaisesRegex(RuntimeError, 'stale'):
                obj.verified_live()

    def test_raw_failure_pipeline_stays_failed_with_sampler_failure_and_manifest(self):
        class FakeChild:
            def __init__(self, argv, out, role, env):
                self.role, self.count = role, 0
                self.receipt = {'role': role, 'argv': argv, 'identity': row(), 'start': m.stamp(), 'end': m.stamp(),
                                'rawExit': original_exit if role == 'original' else sampler_exit, 'cleanupVerified': True}
                self.child = self
                self.pid = 99991 if role == 'original' else 99992
                verdict = '(fail)' if original_exit else '(pass)'
                (out / (role + '.stdout.log')).write_text('native test window: {"name":"pixel-art","actual":{"width":760,"height":653}}\n' + verdict + ' Metal, offscreen > ' + m.TITLE + '\n33 expect() calls\n')
                (out / (role + '.stderr.log')).write_text('synthetic original error' if role == 'original' else 'synthetic permission denied')
            def poll(self):
                self.count += 1
                return None if self.role == 'original' and self.count <= 3 else self.receipt['rawExit']
            def wait(self, timeout):
                return self.receipt['rawExit']
            def observe(self):
                return {}
            def verified_live(self):
                return row()
            def close(self):
                return self.receipt
        source = {'head': 'synthetic-source'}
        deps = {'binary': BINARY}
        for original_exit, sampler_exit in ((17, 13), (0, 13), (0, 0)):
            out = self.out / (str(original_exit) + '-' + str(sampler_exit))
            release = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'julia-script/foldkit-native',
                       'GITHUB_REF': 'refs/heads/julia/owned-native32-stack-measurement',
                       'GITHUB_EVENT_NAME': 'push', 'GITHUB_RUN_ATTEMPT': '1'}
            with patch.dict(os.environ, release), patch.object(os, 'uname', return_value=type('OS', (), {'sysname': 'Darwin'})()), \
                 patch.object(m, 'source_identity', return_value=source), patch.object(m, 'dependency_identity', return_value=deps), \
                 patch.object(m, 'read_command', side_effect=lambda argv: '1.3.9' if argv[0] == 'bun' else 'v22.22.3' if argv[0] == 'node' else 'synthetic nm'), \
                 patch.object(m, 'Owned', FakeChild):
                actual_exit = m.run(out)
                self.assertEqual(actual_exit, 17 if original_exit else 1, (out / 'result.json').read_text())
            result = json.loads((out / 'result.json').read_text())
            self.assertEqual(result['originalRawExit'], original_exit)
            self.assertEqual(result['samplerRawExit'], sampler_exit)
            self.assertEqual(result['originalVerdict'], 'fail' if original_exit else 'pass')
            self.assertFalse(result['completeEvidence'])
            self.assertTrue((out / 'manifest.json').exists())
            self.assertEqual((out / 'original.stderr.log').read_text(), 'synthetic original error')


if __name__ == '__main__':
    unittest.main()
