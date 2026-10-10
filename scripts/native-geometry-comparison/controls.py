"""Safe receipt/fault and owned Python child controls. NEVER load native GPUI.

Positive modeled receipts test predicates, not physical qualification. Explicit
synthetic/skipped receipts are rejected. No local native command is executed.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import contract as c
import run
import supervise as s


def fixtures(baseline="pass", candidate="pass"):
    dependency = {"bindingSha256": "a" * 64, "installedDigest": "b" * 64, "bindingPath": "/installed/binding.node", "testingPath": "/installed/testing.js", "nodePath": "/pinned/node"}
    expected = {name: {"head": sha, "files": {"original": "c" * 64}, "digest": "d" * 64} for name, sha in c.CASES.items()}
    receipt = {"executionKind": "hosted-native", "platform": "Darwin", "machine": "arm64", "githubRepository": "julia-script/foldkit-native", "githubRunId": "123", "githubHead": "e" * 40, "driverHead": "e" * 40, "driverSourceBefore": {"head": "e" * 40}, "driverSourceAfter": {"head": "e" * 40}, "dependencyAfter": dependency, "bun": "1.3.9", "node": "v22.22.3", "installSupervision": {"started": True, "returncode": 0, "reaped": True, "groupClean": True, "deadlineExceeded": False, "elapsedSeconds": 1, "command": ["npm", "ci", "--ignore-scripts"]}}
    cap = {"nativeAvailable": True, "platform": "darwin", "arch": "arm64", "bun": "1.3.9", "modulePath": dependency["testingPath"], "bindingPath": dependency["bindingPath"], "methods": {name: True for name in c.METHODS}}
    cases = {}
    for index, (name, outcome) in enumerate(zip(c.CASES, (baseline, candidate))):
        state = {"started": True, "returncode": 0 if outcome == "pass" else 7, "reaped": True, "groupClean": True, "deadlineExceeded": False, "elapsedSeconds": 2.5, "pgid": 100 + index, "command": ["/pinned/bun", *c.COMMAND], "envelopeSeconds": 60}
        window = {"name": "pixel-art", "requested": {"width": 1024, "height": 653}, "actual": {"width": 1024, "height": 653}}
        drag = {"cells": 1024, "moves": 62, "ms": 1500, "movesPerSecond": 41, "msPerMove": 24.2}
        raw = 'native test window: ' + json.dumps(window) + '\npixel-art Metal drag: ' + json.dumps(drag) + '\n(' + outcome + ') Metal, offscreen > ' + c.TITLE + ' [2500.00ms]\n'
        cases[name] = {"sourceHead": c.CASES[name], "sourcePath": "/owned/" + name, "executionKind": "hosted-native", "command": state["command"], "supervision": state, "capabilitySupervision": {**state, "returncode": 0, "envelopeSeconds": 15}, "capability": copy.deepcopy(cap), "before": {"source": expected[name], "dependencies": copy.deepcopy(dependency)}, "after": {"source": expected[name], "dependencies": copy.deepcopy(dependency)}, "raw": raw, "originalEnvironment": {"FOLDKIT_NATIVE_RENDERER": "gpuix"}, "originalStartedEpoch": 1000 + index * 5, "originalEndedEpoch": 1002.5 + index * 5, "png": {"validPng": True, "width": 1024, "height": 653, "sha256": "f" * 64}}
    return receipt, cases, expected, dependency, "/pinned/bun", {name: True for name in c.CASES}


class Evidence(unittest.TestCase):
    def test_ordinary_main_rejection_persists_full_startup_state_without_children(self):
        root = Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        for name in ('PYTHONDONTWRITEBYTECODE', 'PYTHONPYCACHEPREFIX', 'GITHUB_ACTIONS', 'GITHUB_REPOSITORY', 'GITHUB_SHA'):
            env.pop(name, None)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'evidence'
            command = [sys.executable, str(root / 'scripts/native-geometry-comparison/run.py'), '--out', str(out)]
            result = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 1, result.stderr)
            state = json.loads((out / 'provenance.json').read_text())
            self.assertEqual(state['driverHead'], run.git(root, 'rev-parse', 'HEAD'))
            self.assertEqual(state['driverPorcelain'], subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=all'], text=True))
            self.assertIsNone(state['githubHead'])
            self.assertTrue(state['python']['bytecodeDisabled'])
            self.assertEqual(state['python']['inheritedBytecodePolicy'], '1')
            self.assertNotIn('installSupervision', state)
            for name in c.CASES:
                case = json.loads((out / name / 'case.json').read_text())
                self.assertFalse(case['supervision']['started'])
                self.assertFalse(case['capabilitySupervision']['started'])
            self.assertFalse(list((root / 'scripts/native-geometry-comparison').rglob('*.pyc')))

    def test_ordinary_startup_and_owned_python_import_leave_no_adjacent_cache(self):
        source = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("run.py", "contract.py", "supervise.py"):
                (root / name).write_bytes((source / name).read_bytes())
            code = f"""import sys, runpy, os
from pathlib import Path
sys.pycache_prefix = None
sys.path.insert(0, {str(root)!r})
runpy.run_path({str(root / 'run.py')!r}, run_name='startup_probe')
import supervise
state = supervise.supervise([sys.executable, '-c', 'import contract,sys;assert sys.dont_write_bytecode'], Path({str(root / 'child.log')!r}), os.environ)
assert state['returncode'] == 0 and state['reaped'] and state['groupClean']
"""
            env = dict(os.environ)
            env.pop('PYTHONDONTWRITEBYTECODE', None)
            env.pop('PYTHONPYCACHEPREFIX', None)
            child = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertFalse(list(root.rglob('*.pyc')))
            self.assertFalse(list(root.rglob('__pycache__')))

    def test_real_tracked_untracked_and_wrong_head_reject_with_startup_state(self):
        source = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'owned-clone'
            subprocess.run(['git', 'clone', '--shared', '--quiet', str(source), str(root)], check=True, timeout=30)
            head = run.git(root, 'rev-parse', 'HEAD')
            tracked = root / '.github/workflows/native-geometry-comparison.yml'
            original = tracked.read_bytes()
            for mode in ('tracked', 'untracked', 'wrong-head'):
                if mode == 'tracked':
                    tracked.write_bytes(original + b'\n# owned tracked drift\n')
                elif mode == 'untracked':
                    (root / 'owned-extra').mkdir()
                    for name in ('one', 'two'):
                        (root / 'owned-extra' / name).write_text('owned untracked drift')
                env = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'julia-script/foldkit-native', 'GITHUB_RUN_ID': '123', 'GITHUB_SHA': '0' * 40}
                with patch.dict(os.environ, env), patch.object(run, 'supervise', side_effect=AssertionError('non-git child must not start')) as spawn:
                    receipt, cases = run.seed(Path(directory) / (mode + '-state'))
                    with self.assertRaisesRegex(RuntimeError, 'source head or clean tree changed' if mode != 'wrong-head' else 'released exact-head hosted'):
                        run.execute(root, Path(directory) / (mode + '-state'), receipt, cases, {}, {})
                self.assertEqual(spawn.call_count, 0)
                self.assertEqual(receipt['driverHead'], head)
                self.assertEqual(receipt['githubHead'], '0' * 40)
                self.assertTrue(receipt['python']['bytecodeDisabled'])
                self.assertEqual(receipt['python']['inheritedBytecodePolicy'], '1')
                if mode == 'tracked':
                    self.assertIn(' M .github/workflows/native-geometry-comparison.yml', receipt['driverPorcelain'])
                    tracked.write_bytes(original)
                elif mode == 'untracked':
                    for name in ('one', 'two'):
                        self.assertIn('?? owned-extra/' + name, receipt['driverPorcelain'])
                        (root / 'owned-extra' / name).unlink()
                    (root / 'owned-extra').rmdir()
                else:
                    self.assertEqual(receipt['driverPorcelain'], '')

    def test_positive_modeled_predicates_not_native_qualification(self):
        result = c.validate_all(*fixtures())
        self.assertTrue(result["completeComparisonEvidence"])
        self.assertTrue(result["candidateOriginalTestPassed"])
        self.assertEqual(c.exit_code(result), 0)

    def test_baseline_fail_candidate_pass_keeps_raw_failure(self):
        values = fixtures("fail")
        values[1]["baseline"]["png"] = {"available": False}
        result = c.validate_all(*values)
        self.assertTrue(result["completeComparisonEvidence"])
        self.assertFalse(result["baselineOriginalTestPassed"])
        self.assertTrue(result["candidateOriginalTestPassed"])
        self.assertFalse(result["performanceComparisonAvailable"])
        self.assertEqual(c.exit_code(result), 7)

    def test_candidate_failure_is_not_comparison_acceptance(self):
        result = c.validate_all(*fixtures("pass", "fail"))
        self.assertTrue(result["completeComparisonEvidence"])
        self.assertFalse(result["candidateOriginalTestPassed"])
        self.assertEqual(c.exit_code(result), 7)

    def test_actual_supervised_nonzero_python_child_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "raw.log"
            state = s.supervise([sys.executable, "-c", "print('owned original failure');raise SystemExit(7)"], log, os.environ)
            self.assertEqual(state["returncode"], 7)
            self.assertTrue(state["reaped"] and state["groupClean"])
            self.assertIn("owned original failure", log.read_text())

    def test_owned_timeout_kills_only_owned_python_group(self):
        with tempfile.TemporaryDirectory() as directory:
            state = s.supervise([sys.executable, "-c", "import time;time.sleep(5)"], Path(directory) / "raw.log", os.environ, deadline=0.08)
            self.assertTrue(state["deadlineExceeded"])
            self.assertTrue(state["reaped"] and state["groupClean"])
            self.assertNotEqual(state["returncode"], 0)

    def test_owned_grandchild_is_cleaned_after_parent_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            code = "import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(5)']);print('owned parent done')"
            state = s.supervise([sys.executable, "-c", code], Path(directory) / "raw.log", os.environ)
            self.assertEqual(state["returncode"], 0)
            self.assertTrue(state["reaped"] and state["groupClean"])

    def test_live_group_cleanup_fault_preserves_known_exit(self):
        child = unittest.mock.Mock(pid=123456, returncode=7)
        child.wait.return_value = 7
        state = {}
        with tempfile.TemporaryDirectory() as directory, patch.object(s.subprocess, "Popen", return_value=child), patch.object(s.os, "killpg", side_effect=PermissionError("owned cleanup rejected")), patch.object(s, "group_members", return_value=[123456]):
            with self.assertRaises(PermissionError):
                s.supervise(["owned mock child"], Path(directory) / "raw.log", {}, state=state)
        self.assertEqual(state["returncode"], 7)
        self.assertTrue(state["reaped"])
        self.assertFalse(state["groupClean"])

    def test_identity_mutations_and_cross_case_native_mismatch_reject(self):
        for field in ("source", "dependencies"):
            for phase in ("before", "after"):
                values = fixtures()
                values[1]["candidate"][phase][field] = {"stableButDifferent": True}
                self.assertFalse(c.validate_all(*values)["completeComparisonEvidence"])
        values = fixtures()
        for phase in ("before", "after"):
            values[1]["candidate"][phase]["dependencies"]["bindingSha256"] = "1" * 64
        self.assertFalse(c.validate_all(*values)["completeComparisonEvidence"])

    def test_case_source_and_runtime_capability_native_rejection(self):
        for key, value in [("nativeAvailable", False), ("platform", "linux"), ("bun", "1.3.10"), ("bindingPath", "/other/binding.node")]:
            values = fixtures()
            values[1]["candidate"]["capability"][key] = value
            self.assertFalse(c.validate_all(*values)["completeComparisonEvidence"])
        values = fixtures()
        values[1]["candidate"]["sourceHead"] = c.BASE
        self.assertFalse(c.validate_all(*values)["completeComparisonEvidence"])

    def test_skipped_missing_duplicate_other_test_and_exit_disagreement_reject(self):
        for raw in ["", "(skip) " + c.TITLE + " [1ms]", "(pass) other test [1ms]\n", "(pass) " + c.TITLE + " [1ms]\n(pass) " + c.TITLE + " [1ms]"]:
            values = fixtures()
            values[1]["baseline"]["raw"] = raw
            self.assertFalse(c.validate_all(*values)["completeComparisonEvidence"])
        values = fixtures()
        values[1]["baseline"]["supervision"]["returncode"] = 7
        self.assertFalse(c.validate_all(*values)["completeComparisonEvidence"])

    def test_synthetic_or_changed_command_and_missing_window_png_reject(self):
        for key, value in [("executionKind", "synthetic"), ("command", ["bun", "test", "--timeout", "60000"]), ("png", {})]:
            values = fixtures()
            values[1]["candidate"][key] = value
            self.assertFalse(c.validate_all(*values)["completeComparisonEvidence"])
        values = fixtures()
        values[0]["executionKind"] = "synthetic"
        self.assertFalse(c.validate_all(*values)["completeComparisonEvidence"])
        values = fixtures()
        values[1]["candidate"]["raw"] = '(pass) ' + c.TITLE + ' [2500ms]'
        self.assertFalse(c.validate_all(*values)["completeComparisonEvidence"])

    def test_serial_separate_sessions_and_clean_groups_required(self):
        for mutate in [lambda v: v[1]["candidate"]["supervision"].update(pgid=100), lambda v: v[1]["candidate"].update(originalStartedEpoch=1001), lambda v: v[1]["candidate"]["supervision"].update(groupClean=False), lambda v: v[1]["baseline"]["supervision"].update(deadlineExceeded=True), lambda v: v[5].update(baseline=False)]:
            values = fixtures(); mutate(values)
            self.assertFalse(c.validate_all(*values)["completeComparisonEvidence"])

    def test_malformed_receipts_do_not_accept(self):
        for key, value in [("supervision", None), ("before", None), ("png", None), ("raw", None)]:
            values = fixtures()
            values[1]["candidate"][key] = value
            self.assertFalse(c.validate_all(*values)["completeComparisonEvidence"])

    def main_failure(self, injected):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "evidence"
            with contextlib.redirect_stdout(io.StringIO()), injected:
                result = run.main(["--out", str(out)])
            files = ["provenance.json", "error.json", "cleanup.json", "comparison.json", "baseline/case.json", "candidate/case.json"]
            self.assertTrue(all((out / name).exists() for name in files))
            summary = json.loads((out / "comparison.json").read_text())
            provenance = json.loads((out / "provenance.json").read_text())
            self.assertEqual(provenance['python']['executable'], sys.executable)
            self.assertTrue(provenance['python']['bytecodeDisabled'])
            self.assertEqual(provenance['python']['inheritedBytecodePolicy'], '1')
            self.assertIn('driverHead', provenance)
            self.assertIn('driverPorcelain', provenance)
            self.assertIn('githubHead', provenance)
            self.assertFalse(summary["completeComparisonEvidence"])
            self.assertNotEqual(result, 0)
            return result, summary

    def test_earliest_git_failure_seeds_unsuccessful_artifacts(self):
        self.main_failure(patch.object(run, "git", side_effect=FileNotFoundError("owned preflight git fault")))

    def test_spawn_failure_seeds_unsuccessful_artifacts(self):
        def execute(root, out, receipt, cases, expected, owned):
            receipt["stage"] = "baseline:spawn"
            s.supervise(["/does-not-exist-owned-test-executable"], out / "baseline/original.log", os.environ, state=cases["baseline"]["supervision"])
        self.main_failure(patch.object(run, "execute", side_effect=execute))

    def test_cleanup_and_validation_fault_preserve_known_raw_exit(self):
        def execute(root, out, receipt, cases, expected, owned):
            modeled = fixtures("fail")
            receipt.update(modeled[0]); cases.update(modeled[1]); expected.update(modeled[2])
            return modeled[3], modeled[4]
        for target in ("cleanup_sources", "validate_all"):
            with patch.object(run, "execute", side_effect=execute):
                result, summary = self.main_failure(patch.object(run, target, side_effect=RuntimeError("owned " + target + " failure")))
            self.assertEqual(result, 7)
            self.assertEqual(summary["cases"]["baseline"]["originalExit"], 7)

    def test_frozen_originals_and_approved_source_delta_are_real(self):
        root = Path(__file__).resolve().parents[2]
        original, candidate = [run.expected_source(root, sha) for sha in (c.BASE, c.CANDIDATE)]
        names = set(original["files"]) | set(candidate["files"])
        changed = {name for name in names if original["files"].get(name) != candidate["files"].get(name)}
        self.assertEqual(changed, c.DELTA)
        for name in ("examples/pixel-art/native.test.ts", "packages/foldkit-gpuix/test/support.ts", "examples/support/harness.ts", "package-lock.json", "bunfig.toml"):
            self.assertEqual(original["files"][name], candidate["files"][name])

    def test_unchanged_original_30s_and_ink_source(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "examples/pixel-art/native.test.ts").read_text()
        self.assertIn("const SLOW = 30_000", source)
        self.assertIn("const INK = '#262427'", source)
        self.assertIn(c.TITLE, source)
        self.assertNotIn("--timeout", c.COMMAND)

    def test_source_cleanup_fault_still_attempts_other_owned_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owned = {name: root / (name + '-source') for name in c.CASES}
            for name, path in owned.items():
                path.mkdir(); (root / name).mkdir()
            cases = {name: {"supervision": {"started": True, "groupClean": True}} for name in c.CASES}
            def remove(command, **options):
                if command[-1] == str(owned["baseline"]):
                    raise RuntimeError('owned remove failure')
                owned["candidate"].rmdir()
                return subprocess.CompletedProcess(command, 0, stdout='owned removed')
            def registered(*args):
                return '\n'.join('worktree ' + str(path) for path in owned.values() if path.exists())
            with patch.object(run.subprocess, "run", side_effect=remove) as observed, patch.object(run, 'git', side_effect=registered):
                result = run.cleanup_sources(root, root, owned, cases)
            self.assertEqual(observed.call_count, 2)
            self.assertEqual(result, {"baseline": False, "candidate": True})
            self.assertIn("cleanupFailure", cases["baseline"])

    def test_absent_owned_directory_with_retained_registry_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'baseline').mkdir()
            source = root / 'absent-owned-source'
            case = {'baseline': {'supervision': {'started': False}}}
            with patch.object(run, 'git', side_effect=['worktree ' + str(source), '']), patch.object(run.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='owned registry removed')) as remove:
                result = run.cleanup_sources(root, root, {'baseline': source}, case)
            self.assertTrue(result['baseline'])
            self.assertEqual(remove.call_count, 1)

    def test_screenshot_structure_and_truncated_chunks_reject(self):
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
        header = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
        valid = header + chunk(b'IDAT', zlib.compress(b'\x00\xff\xff\xff\xff')) + chunk(b'IEND', b'')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'owned-fixture.png'
            self.assertFalse(run.png_receipt(path)['available'])
            for data in [header, valid[:-1], valid[:-12], valid + b'extra', header + chunk(b'IDAT', zlib.compress(b'\x00')) + chunk(b'IEND', b'')]:
                path.write_bytes(data)
                self.assertFalse(run.png_receipt(path)['validPng'])
            path.write_bytes(valid)
            self.assertTrue(run.png_receipt(path)['validPng'])

    def test_runner_rejects_missing_native_before_original_spawn(self):
        values = fixtures()
        expected, dependency, bun = values[2]['baseline'], values[3], values[4]
        case = values[1]['baseline']
        case['supervision'] = {'started': False, 'returncode': None}
        capability = case['capability']
        capability['nativeAvailable'] = False
        commands = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            folder = root / 'evidence'; folder.mkdir()
            (folder / 'original.log').write_text('')
            def capability_only(command, log, env, deadline, state):
                commands.append(command)
                log.write_text('native-comparison-capability:' + json.dumps(capability))
                state.update(started=True, returncode=0, reaped=True, groupClean=True, deadlineExceeded=False, elapsedSeconds=1, envelopeSeconds=15)
            before = Path.cwd()
            try:
                os.chdir(root)
                with patch.object(run, 'supervise', side_effect=capability_only), patch.object(run, 'source_identity', return_value=expected), patch.object(run, 'dependencies', return_value=dependency):
                    with self.assertRaisesRegex(RuntimeError, 'native capability rejected'):
                        run.run_case(root, folder, case, expected, dependency, bun, {'FOLDKIT_NATIVE_RENDERER': 'gpuix', 'FOLDKIT_NATIVE_EVIDENCE': str(folder)})
            finally:
                os.chdir(before)
        self.assertEqual(len(commands), 1)
        self.assertIn('--eval', commands[0])
        self.assertFalse(case['supervision']['started'])

    def test_runtime_source_receipt_reads_and_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'owned-fixture').write_bytes(b'original')
            files = {'owned-fixture': hashlib.sha256(b'original').hexdigest()}
            expected = {'head': c.BASE, 'files': files, 'digest': c.digest(files)}
            with patch.object(run, 'git', side_effect=[c.BASE, '']):
                self.assertEqual(run.source_identity(root, expected), expected)
            (root / 'owned-fixture').write_bytes(b'changed')
            with patch.object(run, 'git', side_effect=[c.BASE, '']):
                with self.assertRaisesRegex(RuntimeError, 'source bytes'):
                    run.source_identity(root, expected)


if __name__ == "__main__":
    unittest.main()
