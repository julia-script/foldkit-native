"""Audited synthetic receipts and harmless owned Python children; no native load."""
import copy
import datetime as dt
import io
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
        self.temp = None
        saved = os.environ.get('FOLDKIT_NATIVE32_SAFE_CONTROLS_EVIDENCE')
        if saved:
            case = Path(saved).resolve() / self._testMethodName
            m.require(not case.is_relative_to(m.ROOT), 'safe case evidence must be outside checkout')
            case.mkdir(exist_ok=False)
        else:
            self.temp = tempfile.TemporaryDirectory()
            case = Path(self.temp.name)
        self.out = case / 'evidence'

    def tearDown(self):
        if self.temp:
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

    def test_wrong_head_and_modified_safe_source_stop_before_children(self):
        with patch.dict(os.environ, {'GITHUB_SHA': 'a' * 40}), patch.object(m, 'source_identity') as source:
            with self.assertRaisesRegex(RuntimeError, 'wrong head'):
                safe.audit(safe.default_plan(), 'b' * 40)
            source.assert_not_called()
        identity = {'driverHashes': {'scripts/owned-native32-stack/controls_test.py': 'a' * 64}}
        with patch.dict(os.environ, {'GITHUB_SHA': 'a' * 40}), patch.object(m, 'source_identity', return_value=identity), \
             patch.object(subprocess, 'check_output', return_value=b'modified uncommitted source'):
            with self.assertRaisesRegex(RuntimeError, 'committed review candidate'):
                safe.audit(safe.default_plan(), 'a' * 40)
        with patch.object(m, 'Owned') as spawn:
            self.assertEqual(safe.execute(self.out, 'ignored', [['bun', 'test']]), 1)
            spawn.assert_not_called()
        self.assertFalse(json.loads((self.out / 'result.json').read_text())['spawned'])

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

    def test_positive_structural_stacks_and_native_attribution(self):
        original, sample = receipts()
        native = '    5 build_element (in gpuix-native.darwin-arm64.node)'
        shapes = {
            'zero-only': native.replace('5 build', '0 build'),
            'junk-only': '    1 NOT-A-CALL-TREE-ROW',
            'negative-only': native.replace('5 build', '-5 build'),
            'missing-image': '    1 build_element',
            'empty-image': '    1 build_element (in )',
        }
        evidence = {'classification': 'synthetic parser controls; not physical stacks', 'rejected': {}}
        for name, frame in shapes.items():
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'positive image-linked') as rejected:
                m.validate_report(report().replace(native, frame), original, sample, BINARY)
            evidence['rejected'][name] = str(rejected.exception)
        other = '    + 5 start (in libdyld.dylib) + 12 [0x1000]'
        value = m.validate_report(report().replace(native, native.replace('5 build', '0 build') + '\n' + other), original, sample, BINARY)
        self.assertFalse(value['symbolAttributionAvailable'])
        self.assertEqual(value['nativeImageFrameLines'], [])
        decorated = m.validate_report(report().replace(native, '    | + 5 build_element (in gpuix-native.darwin-arm64.node) + 123 [0x1000]'), original, sample, BINARY)
        self.assertTrue(decorated['symbolAttributionAvailable'])
        evidence['validNonNative'] = value
        evidence['validDecoratedNative'] = decorated
        self.out.mkdir()
        m.save(self.out, 'structural-parser-controls', evidence)

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

    def test_exact_original_saved_outcomes_and_wrong_title_rejection(self):
        # Existing immutable raw Bun outcomes, not new native executions.
        passed = '(pass) Metal, offscreen > ' + m.TITLE + ' [29647.93ms]\n33 expect() calls\n'
        failed = '(fail) Metal, offscreen > ' + m.TITLE + ' [30121.73ms]\n1 expect() calls\n'
        self.assertEqual(m.test_verdict(passed, {'rawExit': 0}), 'pass')
        self.assertEqual(m.test_verdict(failed, {'rawExit': 1}), 'fail')
        self.assertEqual(m.test_verdict('\x1b[32m' + passed + '\x1b[0m', {'rawExit': 0}), 'pass')
        wrong = ['WRONG TEST ' + m.TITLE + ' > OTHER', m.TITLE + ' suffix',
                 m.TITLE + ' [not-a-time]', m.TITLE + ' [29647.93ms] > OTHER']
        rejected = {}
        for title in wrong:
            raw = '(pass) Metal, offscreen > ' + title + '\n33 expect() calls\n'
            with self.subTest(title=title), self.assertRaisesRegex(RuntimeError, 'exactly once') as error:
                m.test_verdict(raw, {'rawExit': 0})
            rejected[title] = str(error.exception)
        self.out.mkdir()
        m.save(self.out, 'exact-outcome-controls', {'classification': 'saved line/parser replay; not new native execution',
               'savedPassLine': passed, 'savedFailLine': failed, 'rejected': rejected})

    def spawn_harmless(self, program, role):
        self.out.mkdir(exist_ok=True)
        argv = [sys.executable, '-c', safe.SAFE_PROGRAMS[program]]
        m.save(self.out, role + '-allowlist', safe.harmless(argv))  # Before each child.
        return m.Owned(argv, self.out, role, {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})

    def test_real_harmless_natural_zero_and_raw_failure_owned_cleanup(self):
        for program, expected in [('success', 0), ('failure', 17)]:
            child = self.spawn_harmless(program, program)
            child.child.wait(timeout=5)
            receipt = child.close()
            m.save(self.out, program + '-process', receipt)
            self.assertEqual(receipt['rawExit'], expected)
            self.assertIsNotNone(receipt['identity'], receipt)
            self.assertTrue(receipt['cleanupVerified'], receipt)
            self.assertFalse(receipt['survivors'])

    def test_real_harmless_timeout_and_known_descendant_cleanup(self):
        for program in ('hang', 'descendant'):
            child = self.spawn_harmless(program, program)
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    child.child.wait(timeout=.15)
                child.observe()
                if program == 'descendant':
                    self.assertGreaterEqual(len(child.receipt['observedGroupMembers']), 2)
            finally:
                receipt = child.close()
                m.save(self.out, program + '-process', receipt)
            self.assertEqual(receipt['rawExit'], -15)
            self.assertIsNotNone(receipt['identity'], receipt)
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

    def test_sampler_permission_spawn_failure_is_separate_and_honest(self):
        self.out.mkdir()
        with self.assertRaises(PermissionError):
            m.Owned(['never-executed-sample'], self.out, 'sampler', os.environ.copy(), factory=lambda *a, **k: (_ for _ in ()).throw(PermissionError('synthetic denied permission')))
        receipt = json.loads((self.out / 'sampler-process.json').read_text())
        self.assertIsNone(receipt['rawExit'])
        self.assertFalse(receipt['cleanupVerified'])
        self.assertIn('PermissionError', receipt['spawnOrIdentityError'])
        self.assertFalse((self.out / 'original-process.json').exists())

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
        obj.known = {123: m.token({**row(123), 'pgid': 99991})}
        with patch.object(m, 'table', return_value={99991: row(), 123: {**row(123), 'pgid': 99991, 'start': 'reused-descendant'}}):
            with self.assertRaisesRegex(RuntimeError, 'stale owned group member'):
                obj.observe()

    def test_nested_cleanup_refuses_reused_pid_and_unknown_inspection(self):
        known = {123: m.token(row(123))}
        with patch.object(m, 'table', return_value={123: {**row(123), 'start': 'reused'}}), patch.object(os, 'kill') as kill:
            receipt = safe.cleanup_nested(known)
            kill.assert_not_called()
            self.assertFalse(receipt['cleanupVerified'])
            self.assertIsNone(receipt['survivors'])
            self.assertIn('signal refused', receipt['error'])
        with patch.object(m, 'table', side_effect=OSError('inspection unavailable')), patch.object(os, 'kill') as kill:
            receipt = safe.cleanup_nested(known)
            kill.assert_not_called()
            self.assertFalse(receipt['cleanupVerified'])
            self.assertIsNone(receipt['survivors'])
            self.assertIn('inspection unavailable', receipt['error'])

    def test_owned_close_is_idempotent_and_registry_drained(self):
        child = self.spawn_harmless('success', 'idempotent')
        self.assertIn(child, m.ACTIVE_OWNED)
        child.child.wait(timeout=5)
        first = copy.deepcopy(child.close())
        self.assertEqual(first, child.close())
        self.assertNotIn(child, m.ACTIVE_OWNED)
        self.assertTrue(first['cleanupVerified'])
        m.save(self.out, 'idempotent-process', first)

    def test_owned_inspection_failure_does_not_acknowledge_empty_cleanup(self):
        child = object.__new__(m.Owned)
        child.closed = False
        child.child = type('Child', (), {'pid': 99991, 'returncode': None, 'poll': lambda self: None})()
        child.stdout, child.stderr = io.StringIO(), io.StringIO()
        child.receipt = {'rawExit': None, 'cleanupVerified': False, 'survivors': None, 'signals': []}
        with patch.object(m, 'table', side_effect=OSError('inspection unavailable')), patch.object(os, 'killpg') as kill:
            receipt = child.close()
            kill.assert_not_called()
        self.assertFalse(receipt['cleanupVerified'])
        self.assertIsNone(receipt['survivors'])
        self.assertIsNone(receipt['rawExit'])
        self.assertIn('inspection unavailable', receipt['cleanupError'])

    def test_cancelled_receipt_publication_is_complete_and_exclusive(self):
        self.out.mkdir()
        value = {'rawExit': -15, 'cleanupVerified': True, 'signal': 15}
        with patch.object(m, 'CANCELLATION_CHECK', side_effect=safe.Cancelled(15)):
            with self.assertRaises(safe.Cancelled):
                m.save(self.out, 'closed', value)
        raw = (self.out / 'closed.json').read_bytes()
        self.assertEqual(json.loads(raw), value)
        self.assertFalse(list(self.out.glob('*.pending-*')))
        with self.assertRaises(FileExistsError):
            m.save(self.out, 'closed', {'wrong': 'replacement'})
        self.assertEqual(raw, (self.out / 'closed.json').read_bytes())

    def test_actual_measurement_entrypoint_deferred_publication(self):
        self.out.mkdir()
        for point in ('result.json', 'manifest.json'):
            out = self.out / point.removesuffix('.json')
            argv = [sys.executable, '-c', safe.SAFE_PROGRAMS['measurement-publication']]
            m.save(self.out, point + '-allowlist', safe.harmless(argv))
            env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'FOLDKIT_SAFE_MEASUREMENT_OUT': str(out),
                   'FOLDKIT_SAFE_PUBLICATION_POINT': point}
            child = m.Owned(argv, self.out, point + '-entrypoint', env)
            try:
                child.child.wait(timeout=10)
            finally:
                receipt = child.close()
                m.save(self.out, point + '-entrypoint-process', receipt)
            self.assertEqual(receipt['rawExit'], 143)
            self.assertTrue(receipt['cleanupVerified'])
            self.assertEqual(receipt['survivors'], [])
            result = json.loads((out / 'result.json').read_text())
            self.assertIn('local native execution forbidden', result['error'])
            self.assertIsNone(result['originalRawExit'])
            self.assertIsNone(result['samplerRawExit'])
            self.assertFalse(result['completeEvidence'])
            interrupt = json.loads((out / 'measurement-interruption.json').read_text())
            self.assertEqual(interrupt['signal'], 15)
            self.assertEqual(interrupt['error'], result['error'])
            self.assertFalse(list(out.rglob('*.pending-*')))
            for path in out.rglob('*.json'):
                json.loads(path.read_text())
            for name in ('manifest.json', 'terminal-manifest.json'):
                path = out / name
                if path.exists():
                    for relative, digest in json.loads(path.read_text()).items():
                        self.assertEqual(m.sha((out / relative).read_bytes()), digest)
            self.assertEqual((out / 'terminal-manifest.json').exists(), point == 'manifest.json')

    def test_real_measurement_handler_at_harmless_spawn_registration(self):
        self.out.mkdir()
        out = self.out / 'registration'
        argv = [sys.executable, '-c', safe.SAFE_PROGRAMS['measurement-spawn-registration']]
        m.save(self.out, 'registration-allowlist', safe.harmless(argv))
        child = m.Owned(argv, self.out, 'registration-supervisor',
                        {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'FOLDKIT_SAFE_MEASUREMENT_OUT': str(out)})
        try:
            child.child.wait(timeout=10)
        finally:
            receipt = child.close()
            m.save(self.out, 'registration-supervisor-process', receipt)
        self.assertEqual(receipt['rawExit'], 143)
        self.assertTrue(receipt['cleanupVerified'])
        result = json.loads((out / 'result.json').read_text())
        self.assertEqual(result['signal'], 15)
        self.assertEqual(result['originalRawExit'], -15)
        self.assertEqual(result['knownLiveAfter'], [])
        self.assertTrue(result['receipt']['cleanupVerified'])
        self.assertEqual(result['receipt']['survivors'], [])
        for relative, digest in json.loads((out / 'manifest.json').read_text()).items():
            self.assertEqual(m.sha((out / relative).read_bytes()), digest)

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
                if mode == 'early-original' and self.role == 'original':
                    return self.receipt['rawExit']
                if mode in ('original-timeout', 'sampler-timeout'):
                    return None
                return None if self.role == 'original' and self.count <= 3 else self.receipt['rawExit']
            def wait(self, timeout):
                if mode == 'sampler-wait-timeout':
                    raise subprocess.TimeoutExpired(self.receipt['argv'], timeout)
                return self.receipt['rawExit']
            def observe(self):
                return {}
            def verified_live(self):
                return row()
            def close(self):
                return self.receipt
        source = {'head': 'synthetic-source'}
        deps = {'binary': BINARY}
        cases = [(17, 13, 'raw-fail'), (0, 13, 'sample-fail'), (0, 0, 'missing-report'),
                 (-15, 0, 'original-timeout'), (0, -15, 'sampler-timeout'),
                 (0, -15, 'sampler-wait-timeout'), (17, None, 'early-original'),
                 (0, 0, 'source-drift'), (0, 0, 'binary-drift'), (17, 13, 'terminal-interrupt')]
        for original_exit, sampler_exit, mode in cases:
            out = self.out / mode
            limits = {**m.LIMITS}
            if mode == 'original-timeout':
                limits['originalEnvelopeSeconds'] = -1
            if mode == 'sampler-timeout':
                limits['samplerEnvelopeSeconds'] = -1
            source_after = {**source, 'head': 'different-source'} if mode == 'source-drift' else source
            deps_after = {'binary': {**BINARY, 'sha256': '0' * 64}} if mode == 'binary-drift' else deps
            release = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'julia-script/foldkit-native',
                       'GITHUB_REF': 'refs/heads/julia/owned-native32-stack-measurement',
                       'GITHUB_EVENT_NAME': 'push', 'GITHUB_RUN_ATTEMPT': '1'}
            clean_env = {key: value for key, value in os.environ.items() if key not in ('FOLDKIT_NATIVE_NO_WINDOW', 'NAPI_RS_NATIVE_LIBRARY_PATH', 'BUN_PRELOAD', 'NODE_OPTIONS')}
            with patch.dict(os.environ, {**clean_env, **release}, clear=True), patch.object(os, 'uname', return_value=type('OS', (), {'sysname': 'Darwin'})()), \
                 patch.object(m, 'source_identity', side_effect=[source, source_after]), patch.object(m, 'dependency_identity', side_effect=[deps, deps_after]), \
                 patch.object(m, 'read_command', side_effect=lambda argv: '1.3.9' if argv[0] == 'bun' else 'v22.22.3' if argv[0] == 'node' else 'synthetic nm'), \
                 patch.object(m, 'Owned', FakeChild), patch.object(m, 'LIMITS', limits), \
                 patch.object(m, 'MEASUREMENT_SIGNAL', 15 if mode == 'terminal-interrupt' else None):
                actual_exit = m.run(out)
                self.assertEqual(actual_exit, 128 - original_exit if original_exit < 0 else original_exit or 1, (out / 'result.json').read_text())
            result = json.loads((out / 'result.json').read_text())
            self.assertEqual(result['originalRawExit'], original_exit)
            self.assertEqual(result['samplerRawExit'], None if mode in ('early-original', 'original-timeout') else sampler_exit)
            self.assertEqual(result['originalVerdict'], 'unproved' if mode in ('source-drift', 'binary-drift') else 'fail' if original_exit else 'pass')
            if mode == 'original-timeout':
                self.assertTrue(result['originalEnvelopeExceeded'])
            if mode == 'sampler-timeout':
                self.assertTrue(result['samplerEnvelopeExceeded'])
            if mode in ('sampler-wait-timeout', 'original-timeout', 'sampler-timeout'):
                self.assertIn('Timeout', result['error'])
            if mode == 'early-original':
                self.assertIn('sampler not launched', result['error'])
                self.assertFalse((out / 'sampler-process.json').exists())
            if mode in ('source-drift', 'binary-drift'):
                self.assertIn('identity changed', result['evidenceError'])
            self.assertFalse(result['completeEvidence'])
            if mode == 'terminal-interrupt':
                self.assertEqual(result['interruptSignal'], 15)
                interrupt = json.loads((out / 'measurement-interruption.json').read_text())
                self.assertEqual(interrupt['originalRawExit'], 17)
                self.assertEqual(interrupt['samplerRawExit'], 13)
            self.assertTrue((out / 'manifest.json').exists())
            self.assertEqual((out / 'original.stderr.log').read_text(), 'synthetic original error')


if __name__ == '__main__':
    safe.install_cancellation()
    code = 1
    try:
        code = 0 if unittest.main(exit=False).result.wasSuccessful() else 1
    except safe.Cancelled as interruption:
        code = 128 + interruption.signum
    finally:
        receipts = safe.finish_active()
        if safe.REQUESTED_SIGNAL:
            code = 128 + safe.REQUESTED_SIGNAL
            saved = os.environ.get('FOLDKIT_NATIVE32_SAFE_CONTROLS_EVIDENCE')
            if saved:
                m.save(Path(saved).parent, 'controls-interruption',
                       {'signal': safe.REQUESTED_SIGNAL, 'exitCode': code, 'activeCleanup': receipts})
    raise SystemExit(code)
