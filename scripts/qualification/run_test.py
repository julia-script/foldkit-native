"""Runner controls only. No native dependencies, fixtures, windows, or recording."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import run
from run import cart_inputs, command, privacy_output, restart_output, restart_source, test_passed, theme_states


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
        def failed_startup(*_args):
            initial = json.loads((destination / "result.json").read_text())
            self.assertEqual(initial["status"], "failed")
            raise RuntimeError("scenario failed")
        try:
            with patch.object(sys, "argv", ["run.py", "72", str(self.out / "target"), str(destination)]), patch.object(run, "qualify", side_effect=failed_startup), patch.object(run, "command", side_effect=[run.HEADS["72"], ""]) as probe:
                self.assertEqual(run.main(), 1)
                self.assertEqual(probe.call_count, 2)
            result = json.loads((destination / "result.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "scenario failed")
        finally:
            signal.signal(signal.SIGTERM, previous)

    def test_other_targets_cannot_launch_and_startup_failures_leave_records(self):
        import signal
        previous = signal.getsignal(signal.SIGTERM)
        try:
            for case in ("70", "71", "81"):
                with patch.object(sys, "argv", ["run.py", case, str(self.out / "target"), str(self.out / "evidence")]), patch.object(run, "qualify") as launch:
                    with self.assertRaisesRegex(RuntimeError, "only permits PR72"):
                        run.main()
                    launch.assert_not_called()
            with self.assertRaises(FileNotFoundError):
                command(["/not/a/real/executable"], self.out, {}, self.out, "spawn", 1)
            record = json.loads((self.out / "spawn.json").read_text())
            self.assertEqual(record["status"], "spawn-failed")
            self.assertNotIn("exit", record)
        finally:
            signal.signal(signal.SIGTERM, previous)

    def test_restart_requires_assertions_restoration_two_launches_and_both_pngs(self):
        import struct
        log = "17 expect() calls\nPixel Art restart: #262427 at the first start, #eeeeee picked, #eeeeee at the second start"
        record = {"status": "passed", "exit": 0, "leaked": [], "survivors": [], "ownedProcesses": [{"pid": pid, "args": "bun examples/open.ts pixel-art"} for pid in (1, 2)]}
        path = self.out / "tracked-restart.json"
        def write():
            path.write_text(json.dumps(record))
        write()
        for name in ("pixel-art-restart-1-picked.png", "pixel-art-restart-2-relaunched.png"):
            # Header-only mock, never claimed as a native screenshot.
            (self.out / name).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + struct.pack(">II", 760, 653) + b"\0" * 120)
        restart_output(log, self.out)
        for bad in (log.replace("17 expect", "6 expect"), log.replace("#eeeeee at the second", "#abcdef at the second"), log.replace("#eeeeee", "#262427"), ""):
            with self.assertRaises(RuntimeError):
                restart_output(bad, self.out)
        record["ownedProcesses"].pop()
        write()
        with self.assertRaisesRegex(RuntimeError, "two actual"):
            restart_output(log, self.out)
        record["ownedProcesses"].append({"pid": 2, "args": "bun examples/open.ts pixel-art"})
        record["survivors"] = [{"pid": 2}]
        write()
        with self.assertRaisesRegex(RuntimeError, "owned cleanup"):
            restart_output(log, self.out)
        record["survivors"] = []
        write()
        (self.out / "pixel-art-restart-2-relaunched.png").unlink()
        with self.assertRaisesRegex(RuntimeError, "missing output"):
            restart_output(log, self.out)

    def test_fixture_hash_drift_fails_closed_with_original_failure_artifacts(self):
        target = self.out / "target"
        fixture = target / "examples/pixel-art/restart.test.ts"
        fixture.parent.mkdir(parents=True)
        fixture.write_text("changed original fixture")
        with self.assertRaisesRegex(RuntimeError, "fixture differs"):
            restart_source(target, self.out, "before")
        row = json.loads((self.out / "restart-source-before.json").read_text())
        self.assertNotEqual(row["sha256"], run.RESTART_SHA256)

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

    def test_cart_requires_in_window_delivery_state_changes_and_complete_paths(self):
        labels = ["Checkout", "Products", "Add to Cart", "Checkout", "Place Order", "Continue Shopping", "Checkout", "Products", "Add to Cart", "Checkout", "Place Order", "Products", "Checkout", "Products", "Add to Cart", "Checkout", "Place Order"]
        delivered = [{"kind": "delivered", "label": label, "id": i, "point": {"x": 410, "y": 645.5}, "bounds": {"x": 350, "y": 638, "width": 120, "height": 32}, "viewport": {"width": 760, "height": 653}} for i, label in enumerate(labels)]
        observed = [{"kind": "observed", "label": row["label"], "id": row["id"], "point": dict(row["point"]), "before": {"text": ["before"]}, "after": {"text": ["after"], "painted": ["after"]}} for row in delivered]
        (self.out / "measured.png").write_bytes(b"mock frame; not native evidence")
        (self.out / "cart-frame-measurements.json").write_text(json.dumps([{"path": str(self.out / "measured.png")}] * len(labels)))
        path = self.out / "cart-inputs.json"
        def write():
            path.write_text(json.dumps(delivered + observed))
        write()
        cart_inputs(self.out)
        # Original centre654 must fail even if a matching acknowledgment exists.
        delivered[0]["point"]["y"] = observed[0]["point"]["y"] = 654
        write()
        with self.assertRaisesRegex(RuntimeError, "out-of-window"):
            cart_inputs(self.out)
        delivered[0]["point"]["y"] = observed[0]["point"]["y"] = 645.5
        observed[0]["after"]["text"] = ["before"]
        write()
        with self.assertRaisesRegex(RuntimeError, "observable state"):
            cart_inputs(self.out)
        observed.pop()
        write()
        with self.assertRaisesRegex(RuntimeError, "delivery/state"):
            cart_inputs(self.out)


if __name__ == "__main__":
    unittest.main()
