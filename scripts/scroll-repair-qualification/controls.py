"""Audited, non-native controls only; modeled receipts are NOT native evidence."""
import sys
sys.dont_write_bytecode = True
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import zlib

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import contract as c
import run
import safe
import supervise as s
ROOT = Path(__file__).resolve().parents[2]
ENV = dict(os.environ)
for key in ('NODE_OPTIONS', 'BUN_OPTIONS', 'BUN_PRELOAD', 'PYTHONPATH', 'PYTHONSTARTUP', 'GITHUB_ACTIONS', 'GITHUB_SHA', 'GITHUB_REPOSITORY', 'GITHUB_RUN_ID'):
    ENV.pop(key, None)


def fixtures(fail=None):
    dependency = {'bindingSha256': 'a' * 64, 'installedDigest': 'b' * 64, 'bindingPath': '/installed/binding.node', 'testingPath': '/installed/testing.js', 'nodePath': '/pinned/node'}
    expected = {name: {'head': spec['head'], 'files': {'original': 'c' * 64}, 'digest': 'd' * 64} for name, spec in c.CASES.items()}
    def state(command, raw=0, pgid=50, envelope=60):
        return {'started': True, 'returncode': raw, 'reaped': True, 'groupClean': True, 'remainingGroupMembers': [], 'naturalExit': True, 'deadlineExceeded': False, 'signals': [], 'elapsedSeconds': 2.5, 'pgid': pgid, 'command': command, 'envelopeSeconds': envelope}
    receipt = {'executionKind': 'hosted-native', 'platform': 'Darwin', 'machine': 'arm64', 'githubRepository': 'julia-script/foldkit-native', 'githubRunId': '123', 'githubHead': 'e' * 40, 'driverHead': 'e' * 40, 'bun': '1.3.9', 'node': 'v22.22.3', 'installSupervision': state(['npm', 'ci', '--ignore-scripts']), 'driverSourceBefore': {'head': 'e' * 40}, 'driverSourceAfter': {'head': 'e' * 40}, 'dependencyIdentity': copy.deepcopy(dependency), 'dependencyAfter': copy.deepcopy(dependency), 'sourceDelta': {'base': c.BASE, 'candidate': c.CANDIDATE, 'files': sorted(c.DELTA)}}
    capability = {'nativeAvailable': True, 'platform': 'darwin', 'arch': 'arm64', 'bun': '1.3.9', 'modulePath': dependency['testingPath'], 'bindingPath': dependency['bindingPath'], 'methods': {method: True for method in c.METHODS}}
    cases = {}
    for index, (name, identity) in enumerate(c.CASES.items()):
        spec = c.SPECS[identity['test']]
        outcome = 'fail' if name == fail else 'pass'
        window = {'name': spec['window'], 'requested': {'width': 1024, 'height': 653}, 'actual': {'width': 1024, 'height': 653}}
        raw = 'native test window: ' + json.dumps(window) + '\n'
        if identity['test'] == 'pixel32':
            raw += 'pixel-art Metal drag: ' + json.dumps({'cells': 1024, 'moves': 62, 'ms': 1500}) + '\n'
        raw += f"({outcome}) Metal, offscreen > {spec['title']} [2500.00ms]\n\n {int(outcome == 'pass')} pass\n {int(outcome == 'fail')} fail\n {spec['assertions'] if outcome == 'pass' else 2} expect() calls\nRan 1 test across 1 file. [2.5s]\n"
        cases[name] = {'name': name, 'sourceHead': identity['head'], 'sourcePath': '/owned/' + name, 'executionKind': 'hosted-native', 'command': c.command_for(name, '/pinned/bun'), 'supervision': state(c.command_for(name, '/pinned/bun'), 7 if outcome == 'fail' else 0, 100 + index), 'capabilitySupervision': state(c.capability_command('/pinned/bun'), pgid=200 + index, envelope=15), 'prepareSupervision': state(['prepare'], pgid=300 + index), 'capability': copy.deepcopy(capability), 'before': {'source': expected[name], 'dependencies': copy.deepcopy(dependency)}, 'after': {'source': expected[name], 'dependencies': copy.deepcopy(dependency)}, 'raw': raw, 'originalEnvironment': {'FOLDKIT_NATIVE_RENDERER': 'gpuix'}, 'originalStartedEpoch': 1000 + index * 5, 'originalEndedEpoch': 1002.5 + index * 5, 'screenshots': {file: {'available': outcome == 'pass', 'validPng': True, 'width': 1024, 'height': 653, 'sha256': 'f' * 64} for file in spec['screenshots']}}
    return receipt, cases, expected, dependency, '/pinned/bun', {name: True for name in c.CASES}


def owned_child(kind, log, deadline=10):
    command = [sys.executable, '-c', safe.HARMLESS[kind]]
    audit = safe.audit_child(ROOT, command, ENV, kind)
    state = s.supervise(command, log, ENV, deadline=deadline)
    log.with_suffix('.audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    log.with_suffix('.supervision.json').write_text(json.dumps(state, indent=2) + '\n')
    return state


class Selection(unittest.TestCase):
    def no_spawn(self, command, env=None, root=ROOT):
        started = []
        with self.assertRaises(safe.Rejected):
            safe.execute(root, command, env or {}, spawn=lambda *a, **k: started.append(a))
        self.assertEqual(started, [])

    def test_exact_safe_commands_and_no_shell(self):
        for command in safe.CHECKS.values():
            calls = []
            sentinel = object()
            result = safe.execute(ROOT, command, ENV, lambda *a, **kw: (calls.append((a, kw)), sentinel)[1])
            self.assertIs(result, sentinel)
            self.assertFalse(calls[0][1]['shell'])
            self.assertEqual(calls[0][0][0], list(command) if command[0] == sys.executable else ['rtk', 'proxy', *command])

    def test_unsafe_selections_never_spawn(self):
        for command in [('bun', 'test'), ('bun', 'test', 'packages/foldkit-gpuix/test/keyboard.test.ts'), ('bun', 'test', 'packages/foldkit-gpuix/test/window.test.ts'), ('bun', 'test', 'packages/foldkit-gpuix/test/metal.test.ts'), ('bun', 'test', 'examples/big-list/native.test.ts'), ('bun', 'test', 'examples/pixel-art/native.test.ts'), ('bun', 'test', 'packages/foldkit-gpuix/test/events.test.ts'), ('bun', 'test', 'packages/foldkit-gpuix/test/*.test.ts'), ('bun', '--eval', 'loadNative()'), ('bun', 'run', 'css'), (*safe.CHECKS['typecheck'], '--preload', 'native.ts'), ('python3', 'unknown.py')]:
            self.no_spawn(command)
        for key in ('NODE_OPTIONS', 'BUN_OPTIONS', 'BUN_PRELOAD', 'PYTHONPATH', 'PYTHONSTARTUP'):
            self.no_spawn(safe.CHECKS['controls'], {key: 'unreviewed'})

    def test_child_program_change_unknown_and_hosted_startup_reject(self):
        with patch.object(s.subprocess, 'Popen') as spawn, tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(safe.Rejected):
                safe.audit_child(ROOT, [sys.executable, '-c', "print('changed')"], ENV, 'success')
            with self.assertRaises(safe.Rejected):
                safe.audit_child(ROOT, ['bun', 'test'], ENV, 'native')
            out = Path(directory) / 'fresh'
            command = [sys.executable, str(ROOT / 'scripts/scroll-repair-qualification/run.py'), '--out', str(out)]
            with self.assertRaises(safe.Rejected):
                safe.audit_child(ROOT, command, {**ENV, 'GITHUB_ACTIONS': 'true'}, 'startup', out)
            spawn.assert_not_called()

    def test_missing_changed_and_escaped_inspected_sources_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.no_spawn(safe.CHECKS['controls'], root=root)
            for name in safe.FINGERPRINTS:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            self.assertTrue(safe.audit(root, safe.CHECKS['controls'], ENV)['accepted'])
            path = root / 'scripts/scroll-repair-qualification/run.py'
            path.write_text(path.read_text() + '\n# changed\n')
            self.no_spawn(safe.CHECKS['controls'], root=root)
            path.unlink(); path.symlink_to(ROOT / 'scripts/scroll-repair-qualification/run.py')
            self.no_spawn(safe.CHECKS['controls'], root=root)


class Evidence(unittest.TestCase):
    def test_four_modeled_receipts_test_predicates_only(self):
        result = c.validate_all(*fixtures())
        self.assertTrue(result['completeComparisonEvidence'])
        self.assertTrue(result['candidateOriginalTestsPassed'])
        self.assertEqual(c.exit_code(result), 0)
        values = fixtures(); values[0]['executionKind'] = 'synthetic'
        self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])

    def test_each_original_failure_preserved_with_other_cases_visible(self):
        for name in c.CASES:
            result = c.validate_all(*fixtures(fail=name))
            self.assertTrue(result['completeComparisonEvidence'], result['problems'])
            self.assertEqual(result['cases'][name]['originalExit'], 7)
            self.assertEqual(c.exit_code(result), 7)
            for other in c.CASES:
                if other != name:
                    self.assertEqual(result['cases'][other]['originalResult'], 'pass')
            if name.startswith('baseline_'):
                self.assertTrue(result['candidateOriginalTestsPassed'])
                self.assertFalse(result['baselineOriginalTestsPassed'])

    def test_skipped_missing_duplicate_wrong_selection_and_summary_reject(self):
        name = 'candidate_big_list'
        for raw in ['', '(skip) Metal, offscreen > ' + c.SPECS['big_list']['title'] + ' [1ms]', '(pass) unrelated [1ms]']:
            values = fixtures(); values[1][name]['raw'] = raw
            self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])
        for old, new in [('8 expect() calls', '7 expect() calls'), ('Ran 1 test across 1 file.', 'Ran 2 tests across 1 file.'), ('1 pass', '2 pass'), ('0 fail', '1 fail')]:
            values = fixtures(); values[1][name]['raw'] = values[1][name]['raw'].replace(old, new)
            self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])
        values = fixtures(); values[1][name]['raw'] *= 2
        self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])
        for name in c.CASES:
            values = fixtures(); values[1][name]['command'].extend(['--timeout', '60000'])
            self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])
        values = fixtures(); values[1].pop('baseline_pixel32')
        self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])

    def test_assertion_counts_and_exit_failure_cannot_be_normalized(self):
        for name in c.CASES:
            spec = c.SPECS[c.CASES[name]['test']]
            values = fixtures(); values[1][name]['raw'] = values[1][name]['raw'].replace(str(spec['assertions']) + ' expect() calls', '0 expect() calls')
            self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])
            values = fixtures(); values[1][name]['supervision']['returncode'] = 143
            result = c.validate_all(*values)
            self.assertFalse(result['completeComparisonEvidence'])
            self.assertEqual(c.exit_code(result), 143)

    def test_all_missing_corrupt_original_pngs_reject_even_on_failure(self):
        for name, spec in c.CASES.items():
            for file in c.SPECS[spec['test']]['screenshots']:
                for png in [{}, {'available': False}, {'available': True, 'validPng': False}]:
                    values = fixtures(); values[1][name]['screenshots'][file] = png
                    self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])
                values = fixtures(fail=name); values[1][name]['screenshots'][file] = {'available': True, 'validPng': False}
                self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])

    def test_source_dependency_native_and_runtime_drift_reject(self):
        for name in c.CASES:
            for phase in ('before', 'after'):
                for field in ('source', 'dependencies'):
                    values = fixtures(); values[1][name][phase][field] = {'stableButWrong': True}
                    self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])
            for key, wrong in [('nativeAvailable', False), ('bun', '1.3.10'), ('arch', 'x64'), ('bindingPath', '/other.node')]:
                values = fixtures(); values[1][name]['capability'][key] = wrong
                self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])
        values = fixtures(); values[0]['dependencyAfter']['bindingSha256'] = '1' * 64
        self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])
        values = fixtures(); values[0]['sourceDelta']['files'].append('unreviewed.ts')
        self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])

    def test_serial_fresh_sessions_cleanup_and_natural_exit_required(self):
        name = 'candidate_pixel32'
        for key, wrong in [('pgid', 100), ('groupClean', False), ('reaped', False), ('naturalExit', False), ('deadlineExceeded', True), ('signals', ['SIGTERM']), ('inspectionFailure', {'type': 'fault'})]:
            values = fixtures(); values[1][name]['supervision'][key] = wrong
            self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])
        values = fixtures(); values[1][name]['originalStartedEpoch'] = 1001
        self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])
        values = fixtures(); values[1][name]['sourcePath'] = '/owned/baseline_big_list'
        self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])
        values = fixtures(); values[5][name] = False
        self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])

    def test_malformed_receipts_and_missing_drag_reject(self):
        for key in ('supervision', 'before', 'after', 'raw', 'capability', 'screenshots'):
            values = fixtures(); values[1]['candidate_pixel32'][key] = None
            self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])
        values = fixtures(); values[1]['candidate_pixel32']['raw'] = values[1]['candidate_pixel32']['raw'].replace('"moves": 62', '"moves": 61')
        self.assertFalse(c.validate_all(*values)['completeComparisonEvidence'])

    def test_valid_png_then_truncated_corrupt_extra_data_rejected(self):
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
        data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\0\0\0\0\xff')) + chunk(b'IEND', b'')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.png'
            self.assertEqual(run.png_receipt(path), {'available': False})
            path.write_bytes(data)
            self.assertTrue(run.png_receipt(path)['validPng'])  # Byte validation only, never native evidence.
            for invalid in (data[:24], data[:-1], data + b'extra', data[:-5] + b'wrong'):
                path.write_bytes(invalid); self.assertFalse(run.png_receipt(path)['validPng'])

    def test_immutable_source_delta_and_original_contract(self):
        # Read-only git metadata is bounded; no test/native process is loaded.
        safe.audit_sources(ROOT, ENV)
        base, candidate = [run.expected_source(ROOT, head) for head in (c.BASE, c.CANDIDATE)]
        changed = {name for name in set(base['files']) | set(candidate['files']) if base['files'].get(name) != candidate['files'].get(name)}
        self.assertEqual(changed, c.DELTA)
        for name, sha in base['files'].items():
            if name not in c.DELTA:
                self.assertEqual(candidate['files'][name], sha)
        source = (ROOT / 'examples/pixel-art/native.test.ts').read_text()
        self.assertIn('const SLOW = 30_000', source); self.assertIn("const INK = '#262427'", source)
        for name in c.CASES:
            self.assertNotIn('--timeout', c.command_for(name, 'bun'))
            self.assertEqual(c.command_for(name, 'bun')[1:4], ['test', '--preload', './test/support/dom.ts'])


class Ownership(unittest.TestCase):
    def setUp(self):
        if os.environ.get('SCROLL_SAFE_RECEIPTS'):
            receipts = Path(os.environ['SCROLL_SAFE_RECEIPTS']).resolve()
            if receipts.is_relative_to(ROOT):
                raise safe.Rejected('Harmless receipts must be external')
            receipts.mkdir(parents=True, exist_ok=True)
            self.folder = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=receipts))
        else:
            self.owner = tempfile.TemporaryDirectory(prefix='scroll-safe-controls-')
            self.addCleanup(self.owner.cleanup)
            self.folder = Path(self.owner.name)

    def test_natural_success_and_original_nonzero_are_reaped_without_signals(self):
        for kind, code in [('success', 0), ('failure', 7), ('bytecode', 0)]:
            old = Path.cwd()
            try:
                os.chdir(Path(__file__).resolve().parent)
                state = owned_child(kind, self.folder / (kind + '.log'))
            finally:
                os.chdir(old)
            self.assertEqual(state['returncode'], code)
            self.assertTrue(c.cleaned(state), state)
            self.assertEqual(state['signals'], [])
            self.assertFalse(list(Path(__file__).resolve().parent.rglob('*.pyc')))

    def test_deadline_and_retained_grandchild_are_cleaned_but_never_success(self):
        for kind, deadline in [('timeout', 0.08), ('grandchild', 10)]:
            state = owned_child(kind, self.folder / (kind + '.log'), deadline)
            self.assertTrue(state['reaped'] and state['groupClean'], state)
            self.assertFalse(c.cleaned(state))
            self.assertTrue(state['signals'])
            if kind == 'timeout':
                self.assertTrue(state['deadlineExceeded']); self.assertNotEqual(state['returncode'], 0)
            else:
                self.assertEqual(state['returncode'], 0)

    def test_inspection_and_live_cleanup_fault_preserve_raw_exit(self):
        child = Mock(pid=123456, returncode=7)
        child.wait.return_value = 7; child.poll.return_value = 7
        for mode in ('inspection', 'signal'):
            with patch.object(s.subprocess, 'Popen', return_value=child), patch.object(s, 'group_members', side_effect=RuntimeError('owned inspection fault') if mode == 'inspection' else None, return_value=[123456]), patch.object(s.os, 'killpg', side_effect=PermissionError('owned signal fault')):
                state = s.supervise(['owned mocked child'], self.folder / (mode + '.log'), {}, state={})
            self.assertEqual(state['returncode'], 7)
            self.assertTrue(state['reaped'])
            self.assertFalse(state['groupClean'])
            self.assertFalse(c.cleaned(state))

    def test_ordinary_entrypoint_rejects_before_install_capability_or_native(self):
        out = self.folder / 'ordinary'
        env = dict(ENV)
        env.pop('PYTHONDONTWRITEBYTECODE', None); env.pop('PYTHONPYCACHEPREFIX', None)
        command = [sys.executable, str(ROOT / 'scripts/scroll-repair-qualification/run.py'), '--out', str(out)]
        audit = safe.audit_child(ROOT, command, env, 'startup', out)
        state = s.supervise(command, self.folder / 'startup.log', env, deadline=30)
        (self.folder / 'startup.audit.json').write_text(json.dumps(audit))
        (self.folder / 'startup.supervision.json').write_text(json.dumps(state, indent=2))
        self.assertEqual(state['returncode'], 1); self.assertTrue(c.cleaned(state), state)
        receipt = json.loads((out / 'provenance.json').read_text())
        self.assertTrue(receipt['python']['bytecodeDisabled'])
        self.assertEqual(receipt['python']['inheritedBytecodePolicy'], '1')
        self.assertIsNone(receipt['githubHead'])
        self.assertNotIn('installSupervision', receipt)
        for name in c.CASES:
            case = json.loads((out / name / 'case.json').read_text())
            self.assertFalse(case['supervision']['started']); self.assertFalse(case['capabilitySupervision']['started'])
        self.assertFalse(list(Path(__file__).resolve().parent.rglob('*.pyc')))
        # Output reuse must fail before overwriting any immutable receipt.
        before = {str(path.relative_to(out)): hashlib.sha256(path.read_bytes()).hexdigest() for path in out.rglob('*') if path.is_file()}
        with self.assertRaises(FileExistsError):
            run.seed(out)
        after = {str(path.relative_to(out)): hashlib.sha256(path.read_bytes()).hexdigest() for path in out.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def failure_artifacts(self, injected):
        out = self.folder / 'failure'
        with contextlib.redirect_stdout(io.StringIO()), injected:
            code = run.main(['--out', str(out)])
        self.assertNotEqual(code, 0)
        summary = json.loads((out / 'comparison.json').read_text())
        self.assertFalse(summary['completeComparisonEvidence'])
        self.assertFalse(summary['candidateOriginalTestsPassed'])
        for name in c.CASES:
            self.assertTrue((out / name / 'case.json').exists())
        self.assertTrue((out / 'error.json').exists()); self.assertTrue((out / 'cleanup.json').exists())
        return code, summary

    def test_earliest_preflight_failure_seeds_all_four_case_artifacts(self):
        self.failure_artifacts(patch.object(run, 'git', side_effect=FileNotFoundError('owned git failure')))

    def test_original_spawn_fault_has_unsuccessful_supervision(self):
        def execute(root, out, receipt, cases, expected, owned):
            with patch.object(s.subprocess, 'Popen', side_effect=FileNotFoundError('owned spawn fault')):
                s.supervise(['owned mocked command'], out / 'baseline_big_list/original.log', {}, state=cases['baseline_big_list']['supervision'])
            raise RuntimeError('original spawn rejected')
        _, summary = self.failure_artifacts(patch.object(run, 'execute', side_effect=execute))
        self.assertIsNone(summary['cases']['baseline_big_list']['originalExit'])

    def test_cleanup_and_validator_fault_keep_known_raw_baseline_failure(self):
        def execute(root, out, receipt, cases, expected, owned):
            values = fixtures('baseline_big_list'); receipt.update(values[0]); cases.update(values[1]); expected.update(values[2])
            return values[3], values[4]
        for target in ('cleanup_sources', 'validate_all'):
            with tempfile.TemporaryDirectory() as directory, patch.object(run, 'execute', side_effect=execute), patch.object(run, target, side_effect=RuntimeError('owned failure')), contextlib.redirect_stdout(io.StringIO()):
                out = Path(directory) / 'fresh'
                code = run.main(['--out', str(out)])
                result = json.loads((out / 'comparison.json').read_text())
                self.assertEqual(code, 7); self.assertEqual(result['cases']['baseline_big_list']['originalExit'], 7)
                self.assertFalse(result['completeComparisonEvidence'])

    def test_actual_serial_driver_loop_keeps_baseline_failure_and_runs_all_four(self):
        values = fixtures('baseline_big_list')
        root = self.folder / 'driver'; root.mkdir()
        out = self.folder / 'loop'; receipt, cases = run.seed(out)
        observed = []
        def git(_root, *args):
            if args[0] == 'rev-parse': return 'e' * 40
            if args[0] == 'merge-base': return c.BASE
            if args[0] == 'diff': return '\n'.join(sorted(c.DELTA if args[-1] == c.CANDIDATE else c.DRIVER))
            return ''
        def expected(_root, head):
            return {'head': head, 'files': {'original': 'c' * 64}, 'digest': 'd' * 64}
        def prepare(command, log, env, **options):
            options['state'].update(values[0]['installSupervision'])
            if command[0] == 'git': Path(command[-2]).mkdir()
        def original(source, folder, case, expected, dependency, bun, env):
            observed.append(case['name'])
            case.update(copy.deepcopy(values[1][case['name']]))
        hosted = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'julia-script/foldkit-native', 'GITHUB_RUN_ID': '123', 'GITHUB_SHA': 'e' * 40}
        with patch.dict(os.environ, hosted, clear=True), patch.object(run, 'git', side_effect=git), patch.object(run, 'expected_source', side_effect=expected), patch.object(run, 'source_identity', return_value={'head': 'e' * 40}), patch.object(run.platform, 'system', return_value='Darwin'), patch.object(run.platform, 'machine', return_value='arm64'), patch.object(run.shutil, 'which', side_effect=lambda n: '/pinned/' + n), patch.object(run.subprocess, 'check_output', side_effect=lambda command, **kw: '' if command[0] == 'git' else '1.3.9' if command[0].endswith('bun') else 'v22.22.3'), patch.object(run, 'supervise', side_effect=prepare), patch.object(run, 'dependencies', return_value=values[3]), patch.object(run, 'run_case', side_effect=original):
            run.execute(root, out, receipt, cases, {}, {})
        self.assertEqual(observed, list(c.CASES))
        self.assertEqual(cases['baseline_big_list']['supervision']['returncode'], 7)
        self.assertEqual(cases['candidate_pixel32']['supervision']['returncode'], 0)

    def test_unowned_worktree_cannot_be_removed(self):
        cases = {'baseline_big_list': {'supervision': {'started': False}}}
        with patch.object(run.subprocess, 'run') as remove:
            result = run.cleanup_sources(ROOT, self.folder, {'baseline_big_list': self.folder / 'unowned'}, cases)
        remove.assert_not_called(); self.assertFalse(result['baseline_big_list'])
        self.assertEqual(cases['baseline_big_list']['cleanupFailure']['type'], 'OwnershipRejected')

    def test_real_strict_tracked_untracked_wrong_head_and_dirty_startup(self):
        source = self.folder / 'owned-clone'
        command = ['git', 'clone', '--shared', '--quiet', str(ROOT), str(source)]
        audit = safe.audit_child(ROOT, command, ENV, 'clone', source)
        state = s.supervise(command, self.folder / 'clone.log', ENV, deadline=30)
        (self.folder / 'clone.audit.json').write_text(json.dumps(audit, indent=2))
        (self.folder / 'clone.supervision.json').write_text(json.dumps(state, indent=2))
        self.assertEqual(state['returncode'], 0); self.assertTrue(c.cleaned(state))
        safe.audit_sources(ROOT, ENV)
        head = run.git(source, 'rev-parse', 'HEAD')
        safe.audit_sources(ROOT, ENV)
        expected = run.expected_source(source, head)
        tracked = source / 'package.json'; original = tracked.read_bytes()
        for mode in ('tracked', 'untracked', 'wrong-head'):
            if mode == 'tracked': tracked.write_bytes(original + b'\n')
            elif mode == 'untracked': (source / 'unreviewed-extra').write_text('owned untracked control')
            hosted = {**ENV, 'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'julia-script/foldkit-native', 'GITHUB_RUN_ID': '123', 'GITHUB_SHA': '0' * 40}
            with patch.dict(os.environ, hosted, clear=True):
                receipt, cases = run.seed(self.folder / ('strict-' + mode))
            safe.audit_sources(ROOT, ENV)
            with patch.dict(os.environ, hosted, clear=True), patch.object(run, 'supervise', side_effect=AssertionError('install/native must never start')) as spawn:
                with self.assertRaises(RuntimeError):
                    run.execute(source, self.folder / ('strict-' + mode), receipt, cases, {}, {})
            spawn.assert_not_called()
            self.assertEqual(receipt['driverHead'], head)
            self.assertEqual(receipt['githubHead'], '0' * 40)
            if mode == 'tracked':
                self.assertIn(' M package.json', receipt['driverPorcelain']); tracked.write_bytes(original)
            elif mode == 'untracked':
                self.assertIn('?? unreviewed-extra', receipt['driverPorcelain']); (source / 'unreviewed-extra').unlink()
            else:
                self.assertEqual(receipt['driverPorcelain'], '')
        self.assertEqual(run.source_identity(source, expected)['head'], head)

    def test_wrong_head_tracked_untracked_and_before_after_bytes_reject(self):
        expected = {'head': c.BASE, 'files': {'source': hashlib.sha256(b'original').hexdigest()}}
        path = self.folder / 'source'; path.write_bytes(b'original')
        for mode in ('wrong-head', 'tracked', 'untracked', 'byte-drift'):
            if mode == 'byte-drift': path.write_bytes(b'changed')
            with patch.object(run, 'git', side_effect=[c.CANDIDATE if mode == 'wrong-head' else c.BASE, ' M source' if mode == 'tracked' else '?? unexpected' if mode == 'untracked' else '']):
                with self.assertRaises(RuntimeError):
                    run.source_identity(self.folder, expected)
            path.write_bytes(b'original')

    def test_native_capability_rejection_never_spawns_original(self):
        case = fixtures()[1]['baseline_big_list']
        case['capabilitySupervision'] = {}; case['supervision'] = {'started': False}
        folder = self.folder
        (folder / 'original.log').write_text('')
        def capability(command, log, env, **options):
            options['state'].update(started=True, reaped=True, returncode=0, groupClean=True, naturalExit=True, signals=[], deadlineExceeded=False, remainingGroupMembers=[], elapsedSeconds=1)
            log.write_text('scroll-qualification-capability:' + json.dumps({'nativeAvailable': False}) + '\n')
        with patch.object(run, 'source_identity', return_value={}), patch.object(run, 'dependencies', return_value={}), patch.object(run, 'supervise', side_effect=capability) as spawn:
            with self.assertRaisesRegex(RuntimeError, 'real native capability rejected'):
                run.run_case(Path.cwd(), folder, case, {}, {'nodePath': 'mock'}, 'mock', {'FOLDKIT_NATIVE_RENDERER': 'gpuix', 'FOLDKIT_NATIVE_EVIDENCE': str(folder)})
        self.assertEqual(spawn.call_count, 1); self.assertFalse(case['supervision']['started'])


if __name__ == '__main__':
    # The outer safe runner audits this exact closure before this entrypoint;
    # every real child below repeats its specific audit before spawning.
    safe.audit(ROOT, [sys.executable, *sys.argv], os.environ)
    unittest.main()
