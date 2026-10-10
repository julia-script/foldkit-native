"""Pure synthetic validator and owned-process controls. No native imports."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
sys.dont_write_bytecode = True
import run
from run import supervise
from validate import TITLE, validate


def fixture():
    names = ["open.lower", "open.example", "app.update", "app.view", "native.flush", "native.drainEvents", "renderer.applyBatch", "renderer.getWindowSize", "renderer.getElementBounds", "timer.wait.0", "settle", "renderer.dispatchMouseMove", "renderer.captureScreenshot"]
    metric = lambda calls: dict(calls=calls, completed=calls, errors=0, inFlight=0, postClose=0, ms=1, maxMs=1)
    metrics = {f"drag/outside/{name}": metric(62 if name == "renderer.dispatchMouseMove" else 1) for name in names}
    for cycle in range(1, 5):
        for name in ("host.frame", "host.flush", "renderer.flush", "host.drawn", "renderer.dispatchNativeEvents", "native.flush", "native.drainEvents", "timer.wait.0", "timer.callback.0"):
            metrics[f"drag/cycle{cycle}/{name}"] = metric(1)
    resources = dict(cpu={"user": 1}, memory={"rss": 1}, usage={"maxRSS": 1}, load=[0, 0, 0], totalMemory=1, freeMemory=1)
    profile = dict(schema=1, stage="exit", exitCode=0, sourceHead="head", renderer="real-native", instances=1,
                   realBacking=True, platform="darwin", bun="1.3.9", closeCalls=1, closeCompleted=1,
                   settling=False, faults=[], requested=dict(width=1100, height=820), actual=dict(width=1024, height=653),
                   before=resources, after=resources, metrics=metrics, mutations=100, mutationBatches=2)
    return f"(pass) Metal, offscreen > {TITLE} [123.00ms]\n", profile, dict(returncode=0, deadlineExceeded=False, reaped=True, groupClean=True)


class Controls(unittest.TestCase):
    def test_every_cycle_phase_is_required_with_nested_native_lower_bounds(self):
        raw, profile, child = fixture()
        for cycle in range(1, 5):
            for name in ("host.frame", "host.flush", "renderer.flush", "host.drawn", "renderer.dispatchNativeEvents", "native.flush", "native.drainEvents", "timer.wait.0", "timer.callback.0"):
                key = f"drag/cycle{cycle}/{name}"
                for replacement in (None, {**profile["metrics"][key], "inFlight": 1}, {**profile["metrics"][key], "calls": 0, "completed": 0}):
                    changed = copy.deepcopy(profile)
                    if replacement is None:
                        del changed["metrics"][key]
                    else:
                        changed["metrics"][key] = replacement
                    self.assertFalse(validate(raw, changed, child, "head")["completeEvidence"], (key, replacement))
        extra = copy.deepcopy(profile)
        for metric in (extra["metrics"]["drag/cycle4/native.flush"], extra["metrics"]["drag/cycle4/native.drainEvents"]):
            metric["calls"] = metric["completed"] = 3
        self.assertTrue(validate(raw, extra, child, "head")["completeEvidence"])
        # One phase's completed work cannot supply another phase's missing cycle.
        cross_phase = copy.deepcopy(profile)
        for key, metric in list(cross_phase["metrics"].items()):
            if "/cycle" in key or key.endswith("/settle"):
                cross_phase["metrics"][key.replace("drag/", "press/", 1)] = copy.deepcopy(metric)
        del cross_phase["metrics"]["drag/cycle4/host.drawn"]
        self.assertFalse(validate(raw, cross_phase, child, "head")["completeEvidence"])

    def test_malformed_metrics_and_resources_fail_closed(self):
        raw, profile, child = fixture()
        for changes in ({"metrics": None}, {"metrics": []}, {"metrics": {"bad": None}}, {"metrics": {"bad": {"calls": "one"}}}, {"before": None}, {"mutations": None}):
            self.assertFalse(validate(raw, {**profile, **changes}, child, "head")["completeEvidence"], changes)

    def run_main(self, folder):
        previous = Path.cwd()
        try:
            with patch("run.sys.argv", ["run.py", "--out", str(Path(folder) / "evidence")]):
                code = run.main()
        finally:
            os.chdir(previous)
        out = Path(folder) / "evidence"
        artifacts = {name: json.loads((out / name).read_text()) for name in ("provenance.json", "profile.json", "supervision.json", "validation.json")}
        self.assertTrue((out / "original.log").exists())
        self.assertFalse(artifacts["validation.json"]["completeEvidence"])
        self.assertNotEqual(code, 0)
        return code, artifacts

    def preflight(self, command, **kwargs):
        if command[:2] == ["git", "rev-parse"]:
            return "head\n"
        if command[:2] == ["git", "diff"]:
            return "scripts/pixel-art-drag-profile/run.py\n"
        if command[:2] == ["git", "show"]:
            return (Path(run.__file__).resolve().parents[2] / command[2].split(":", 1)[1]).read_bytes()
        if command == ["bun", "--version"]:
            return "1.3.9\n"
        if command == ["node", "--version"]:
            return "v22.22.3\n"
        if command[0] == "git":
            return ""
        raise AssertionError(command)

    def test_earliest_preflight_and_spawn_failures_have_structured_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            def earliest(*args, **kwargs):
                out = Path(folder) / "evidence"
                self.assertEqual({p.name for p in out.iterdir()}, {"original.log", "profile.json", "provenance.json", "supervision.json", "validation.json", "error.json"})
                raise FileNotFoundError("git absent")
            with patch("run.subprocess.check_output", side_effect=earliest), patch("run.subprocess.Popen") as spawn:
                code, artifacts = self.run_main(folder)
                spawn.assert_not_called()
                self.assertEqual(code, 1)
                self.assertIsNone(artifacts["validation.json"]["originalExit"])
                self.assertFalse(artifacts["supervision.json"]["started"])
                self.assertEqual(artifacts["provenance.json"]["failure"]["type"], "FileNotFoundError")
        with tempfile.TemporaryDirectory() as folder, patch("run.subprocess.check_output", side_effect=self.preflight), patch("run.subprocess.Popen", side_effect=OSError("spawn denied")):
            _, artifacts = self.run_main(folder)
            self.assertFalse(artifacts["supervision.json"]["started"])
            self.assertFalse(artifacts["supervision.json"]["reaped"])
            self.assertEqual(artifacts["supervision.json"]["failure"]["stage"], "supervision")

    def test_cleanup_failure_preserves_known_raw_exit_and_unsuccessful_evidence(self):
        for original in (7, 0):
            child = Mock(pid=123, returncode=original)
            child.wait.return_value = original
            with tempfile.TemporaryDirectory() as folder, patch("run.subprocess.check_output", side_effect=self.preflight), patch("run.subprocess.Popen", return_value=child), patch("run.os.killpg", side_effect=PermissionError("live denial")), patch("run.group_members", return_value=[123]):
                code, artifacts = self.run_main(folder)
                self.assertEqual(code, original or 1)
                self.assertEqual(artifacts["validation.json"]["originalExit"], original)
                self.assertEqual(artifacts["supervision.json"]["returncode"], original)
                self.assertFalse(artifacts["supervision.json"]["groupClean"])
                self.assertEqual(artifacts["supervision.json"]["failure"]["type"], "PermissionError")

    def completed_fake(self, command, raw_path, env, state):
        raw, profile, supervision = fixture()
        profile.update(exitCode=7, sourceHead="head", metrics=None)
        supervision["returncode"] = 7
        state.update(supervision, started=True)
        raw_path.write_text(raw.replace("(pass)", "(fail)"))
        Path(env["PIXEL_DRAG_PROFILE_OUTPUT"]).write_text(json.dumps(profile))
        return state

    def test_malformed_profile_and_validator_exception_keep_original_exit(self):
        for validator in (None, ValueError("validation fault")):
            with tempfile.TemporaryDirectory() as folder, patch("run.subprocess.check_output", side_effect=self.preflight), patch("run.supervise", side_effect=self.completed_fake):
                if validator is None:
                    code, artifacts = self.run_main(folder)
                    self.assertIn("metrics must be an object", artifacts["validation.json"]["problems"])
                else:
                    with patch("run.validate", side_effect=validator):
                        code, artifacts = self.run_main(folder)
                    self.assertEqual(artifacts["provenance.json"]["failure"]["stage"], "validation")
                self.assertEqual(code, 7)
                self.assertEqual(artifacts["validation.json"]["originalExit"], 7)

    def test_complete_pass_and_original_failure_are_distinct(self):
        raw, profile, child = fixture()
        self.assertTrue(validate(raw, profile, child, "head")["completeEvidence"])
        profile["exitCode"] = child["returncode"] = 1
        result = validate(raw.replace("(pass)", "(fail)") + "test timed out after 30000ms\n", profile, child, "head")
        self.assertTrue(result["completeEvidence"])
        self.assertEqual(result["originalResult"], "fail")
        self.assertTrue(result["originalTestTimeout"])

    def test_missing_skipped_mocked_partial_and_wrong_provenance_fail(self):
        raw, profile, child = fixture()
        for altered in ("", raw.replace("(pass)", "(skip)"), raw + raw):
            self.assertFalse(validate(altered, profile, child, "head")["completeEvidence"])
        mutations = [{"renderer": "mock"}, {"realBacking": False}, {"stage": "close"}, {"sourceHead": "wrong"},
                     {"closeCompleted": 0}, {"settling": True}, {"faults": ["bad"]}, {"instances": 0},
                     {"before": {}}, {"actual": {}}, {"metrics": {}}, {"exitCode": 1}, {"mutations": 0}]
        for mutation in mutations:
            changed = {**profile, **mutation}
            self.assertFalse(validate(raw, changed, child, "head")["completeEvidence"], mutation)
        partial = copy.deepcopy(profile)
        partial["metrics"]["drag/cycle4/host.frame"]["inFlight"] = 1
        self.assertFalse(validate(raw, partial, child, "head")["completeEvidence"])
        partial = copy.deepcopy(profile)
        del partial["metrics"]["drag/cycle4/host.frame"]
        self.assertFalse(validate(raw, partial, child, "head")["completeEvidence"])
        for change in ({"deadlineExceeded": True}, {"groupClean": False}, {"reaped": False}):
            self.assertFalse(validate(raw, profile, {**child, **change}, "head")["completeEvidence"])

    def test_supervisor_preserves_failure_and_cleans_exit_races(self):
        with tempfile.TemporaryDirectory() as folder:
            result = supervise([sys.executable, "-c", "print('original failure');raise SystemExit(7)"], Path(folder) / "raw", os.environ)
            self.assertEqual(result["returncode"], 7)
            self.assertFalse(result["deadlineExceeded"])
            self.assertTrue(result["groupClean"])
            self.assertTrue(result["reaped"])

    def test_supervisor_kills_only_owned_hanging_child_and_grandchild(self):
        with tempfile.TemporaryDirectory() as folder:
            code = "import subprocess,sys,time,signal;signal.signal(signal.SIGTERM,signal.SIG_IGN);subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);print('owned descendants',flush=True);time.sleep(60)"
            result = supervise([sys.executable, "-c", code], Path(folder) / "raw", os.environ, deadline=0.2)
            self.assertTrue(result["deadlineExceeded"])
            self.assertTrue(result["groupClean"])
            self.assertTrue(result["reaped"])
            self.assertIn("SIGKILL", result["signals"])
            self.assertNotEqual(result["returncode"], 0)

    def test_zombie_only_permission_race_is_accepted_but_live_denial_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("run.os.killpg", side_effect=PermissionError), patch("run.group_members", return_value=[]):
                result = supervise([sys.executable, "-c", "pass"], Path(folder) / "raw", os.environ)
                self.assertTrue(result["groupClean"])
                self.assertIn("group-has-no-live-members", result["signals"])
            with patch("run.os.killpg", side_effect=PermissionError), patch("run.group_members", return_value=[123]):
                with self.assertRaises(PermissionError):
                    supervise([sys.executable, "-c", "pass"], Path(folder) / "raw2", os.environ)


if __name__ == "__main__":
    unittest.main()
