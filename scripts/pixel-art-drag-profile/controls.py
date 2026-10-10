"""Pure synthetic validator and owned-process controls. No native imports."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.dont_write_bytecode = True
from run import supervise
from validate import TITLE, validate


def fixture():
    names = ["open.lower", "open.example", "app.update", "app.view", "native.flush", "native.drainEvents", "renderer.applyBatch", "renderer.getWindowSize", "renderer.getElementBounds", "timer.wait.0", "settle", "renderer.dispatchMouseMove", "renderer.captureScreenshot"]
    metric = lambda calls: dict(calls=calls, completed=calls, errors=0, inFlight=0, postClose=0, ms=1, maxMs=1)
    metrics = {f"drag/outside/{name}": metric(62 if name == "renderer.dispatchMouseMove" else 1) for name in names}
    for cycle in range(1, 5):
        for name in ("host.frame", "host.flush", "host.drawn"):
            metrics[f"drag/cycle{cycle}/{name}"] = metric(1)
    resources = dict(cpu={"user": 1}, memory={"rss": 1}, usage={"maxRSS": 1}, load=[0, 0, 0], totalMemory=1, freeMemory=1)
    profile = dict(schema=1, stage="exit", exitCode=0, sourceHead="head", renderer="real-native", instances=1,
                   realBacking=True, platform="darwin", bun="1.3.9", closeCalls=1, closeCompleted=1,
                   settling=False, faults=[], requested=dict(width=1100, height=820), actual=dict(width=1024, height=653),
                   before=resources, after=resources, metrics=metrics, mutations=100, mutationBatches=2)
    return f"(pass) Metal, offscreen > {TITLE} [123.00ms]\n", profile, dict(returncode=0, deadlineExceeded=False, reaped=True, groupClean=True)


class Controls(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
