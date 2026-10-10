"""Runner controls only. No native dependencies, fixtures, windows, or recording."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import run
from run import command, privacy_output, test_passed, theme_states


class Controls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.out = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def run_command(self, code, timeout=2, grace=0.1):
        return command([sys.executable, "-c", code], self.out, dict(os.environ), self.out, "probe", timeout, grace)

    def test_success_and_failed_exit_remain_distinct(self):
        self.assertEqual(self.run_command("print('original stdout')"), "original stdout\n")
        self.assertEqual(json.loads((self.out / "probe.json").read_text())["status"], "passed")
        with self.assertRaisesRegex(RuntimeError, "command-failed"):
            self.run_command("raise SystemExit(7)")
        self.assertEqual(json.loads((self.out / "probe.json").read_text())["exit"], 7)

    def test_timeout_reaps_owned_group(self):
        with self.assertRaisesRegex(RuntimeError, "timed-out"):
            self.run_command("import time; time.sleep(30)", timeout=0.2)
        self.assertEqual(json.loads((self.out / "probe.json").read_text())["survivors"], [])

    def test_timeout_escalates_only_owned_group_when_term_ignored(self):
        with self.assertRaisesRegex(RuntimeError, "timed-out"):
            self.run_command("import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)", timeout=0.3)
        result = json.loads((self.out / "probe.json").read_text())
        self.assertEqual(result["survivors"], [])
        self.assertEqual(result["exit"], -9)

    def test_successful_parent_with_leaked_child_fails_and_reaps(self):
        with self.assertRaisesRegex(RuntimeError, "leaked-children"):
            self.run_command("import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])")
        result = json.loads((self.out / "probe.json").read_text())
        self.assertTrue(result["leaked"])
        self.assertEqual(result["survivors"], [])

    def test_no_tests_skip_and_failed_test_are_rejected(self):
        test_passed("(pass) expected\n1 pass\n0 fail", "expected")
        for log in ("0 pass\n0 fail", "(pass) expected\n1 pass\n1 skip\n0 fail", "(pass) expected\n1 pass\n1 fail"):
            with self.assertRaises(RuntimeError):
                test_passed(log, "expected")

    def test_failure_preserves_final_source_provenance(self):
        import signal
        previous = signal.getsignal(signal.SIGTERM)
        destination = self.out / "evidence"
        try:
            with patch.object(sys, "argv", ["run.py", "70", str(self.out / "target"), str(destination)]), patch.object(run, "qualify", side_effect=RuntimeError("scenario failed")), patch.object(run, "command", side_effect=[run.HEADS["70"], ""]) as probe:
                self.assertEqual(run.main(), 1)
                self.assertEqual(probe.call_count, 2)
            result = json.loads((destination / "result.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "scenario failed")
        finally:
            signal.signal(signal.SIGTERM, previous)

    def test_privacy_requires_raw_control_masking_reuse_and_progress(self):
        rows = [{"action": action, "rawAtMutation": ["596274"], "stale": {"text": []}, "publicReuse": {"text": ["596274"]}, "frameAtMutation": i + 1} for i, action in enumerate(("clear", "replace", "remove"))]
        path = self.out / "privacy-child-1.json"
        def write():
            path.write_text(json.dumps({"nativePrivacy": rows, "platform": "darwin"}))
        write()
        privacy_output(self.out)
        for key, bad in (("rawAtMutation", []), ("stale", {"text": ["596274"]}), ("publicReuse", {"text": []}), ("frameAtMutation", 0)):
            original = rows[0][key]
            rows[0][key] = bad
            write()
            with self.assertRaises(RuntimeError):
                privacy_output(self.out)
            rows[0][key] = original
        path.unlink()
        with self.assertRaises(RuntimeError):
            privacy_output(self.out)

    def test_recording_requires_both_visible_transitions(self):
        dark = bytes((20, 19, 24)) * (140 * 180)
        light = bytes((244, 241, 234)) * (140 * 180)
        states, _ = theme_states(dark * 2 + light * 2 + dark * 2)
        self.assertEqual([row["state"] for row in states], ["dark", "dark", "light", "light", "dark", "dark"])
        for raw in (dark * 6, light * 6, dark * 2 + light * 4, dark * 3 + light + dark * 2, b""):
            with self.assertRaises(RuntimeError):
                theme_states(raw)
        with self.assertRaises(RuntimeError):
            theme_states(dark * 6, self.out / "failed-transitions.json")
        self.assertEqual(len(json.loads((self.out / "failed-transitions.json").read_text())["samples"]), 6)


if __name__ == "__main__":
    unittest.main()
