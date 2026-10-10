"""Reject vacuous/incomplete profiles; a complete FAILED test remains failed."""
import re
import math

TITLE = "a drag across a 32 × 32 board through GPUI: every cell it crosses, and how many moves a second"
FILTER = "Metal, offscreen.*a drag across a 32"
BASE = "b1073e48f6d72f5745dbd903e320b0bd327818e8"


def validate(raw, profile, supervision, head):
    problems = []
    if not isinstance(profile, dict):
        profile = {}
        problems.append("profile must be an object")
    if not isinstance(supervision, dict):
        supervision = {}
        problems.append("supervision must be an object")
    raw = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    verdicts = re.findall(r"^\((pass|fail|skip)\) (.+)$", raw, re.MULTILINE)
    selected = [(status, title) for status, title in verdicts if TITLE in title]
    if len(selected) != 1 or selected[0][0] == "skip":
        problems.append("original selected test did not execute exactly once")
    if any(TITLE not in title and status != "skip" for status, title in verdicts):
        problems.append("unexpected test executed")
    if supervision.get("deadlineExceeded"):
        problems.append("owned child exceeded supervisor envelope")
    if not supervision.get("reaped") or not supervision.get("groupClean"):
        problems.append("owned process cleanup incomplete")
    if profile.get("stage") != "exit" or profile.get("exitCode") != supervision.get("returncode"):
        problems.append("missing final original-exit summary")
    if profile.get("schema") != 1 or profile.get("sourceHead") != head:
        problems.append("profile provenance mismatch")
    if profile.get("renderer") != "real-native" or profile.get("instances") != 1 or profile.get("realBacking") is not True:
        problems.append("real native renderer path unproved")
    if profile.get("platform") != "darwin" or profile.get("bun") != "1.3.9":
        problems.append("runtime mismatch")
    if profile.get("closeCalls") != 1 or profile.get("closeCompleted") != 1 or profile.get("settling") is not False or profile.get("faults") != []:
        problems.append("observer or harness cleanup incomplete")
    for name in ("requested", "actual"):
        size = profile.get(name, {})
        if not isinstance(size, dict) or not all(isinstance(size.get(axis), (int, float)) and size[axis] > 0 for axis in ("width", "height")):
            problems.append(f"missing {name} dimensions")
    for stage in ("before", "after"):
        resources = profile.get(stage, {})
        if not isinstance(resources, dict) or not all(resources.get(key) for key in ("cpu", "memory", "usage", "load", "totalMemory")) or "freeMemory" not in resources:
            problems.append(f"missing {stage} resources")
    metrics = profile.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
        problems.append("metrics must be an object")
    valid_metrics = {}
    integers = ("calls", "completed", "errors", "inFlight", "postClose")
    for key, metric in metrics.items():
        if not isinstance(key, str) or not isinstance(metric, dict) or any(type(metric.get(field)) is not int or metric[field] < 0 for field in integers) or any(type(metric.get(field)) not in (int, float) or not math.isfinite(metric[field]) or metric[field] < 0 for field in ("ms", "maxMs")):
            problems.append(f"invalid metric {key}")
            continue
        if metric["inFlight"] != 0 or metric["calls"] != metric["completed"] or metric["errors"] > metric["calls"] or metric["postClose"] > metric["calls"] or metric["maxMs"] > metric["ms"]:
            problems.append(f"unbalanced or invalid metric {key}")
        valid_metrics[key] = metric
    metrics = valid_metrics
    def count(suffix):
        return sum(m.get("calls", 0) for key, m in metrics.items() if key.endswith("/" + suffix))
    for name in ("open.lower", "open.example", "app.update", "app.view", "native.flush", "native.drainEvents", "renderer.applyBatch", "renderer.getWindowSize", "renderer.getElementBounds", "host.frame", "host.flush", "host.drawn", "timer.wait.0"):
        if count(name) == 0:
            problems.append(f"missing metric {name}")
    settles = count("settle")
    if settles == 0:
        problems.append("missing settle calls")
    # Check each input phase separately: another phase cannot fill a missing
    # cycle. Native drainEvents may loop; nested paint/drain counts are lower
    # bounds, not invented equalities to the outer helper's counts.
    phases = {key.split("/", 1)[0] for key in metrics if key.endswith("/settle")}
    for phase in phases:
        phase_settles = sum(m["calls"] for key, m in metrics.items() if key.startswith(phase + "/") and key.endswith("/settle"))
        for cycle in range(1, 5):
            prefix = f"{phase}/cycle{cycle}/"
            for name in ("host.frame", "host.flush", "renderer.flush", "host.drawn", "renderer.dispatchNativeEvents", "native.flush", "native.drainEvents", "timer.wait.0", "timer.callback.0"):
                metric = metrics.get(prefix + name, {})
                calls = metric.get("calls", 0)
                required = phase_settles
                if name == "native.flush":
                    required = max(required, metrics.get(prefix + "renderer.flush", {}).get("calls", 0))
                if name == "native.drainEvents":
                    required = max(required, metrics.get(prefix + "renderer.dispatchNativeEvents", {}).get("calls", 0))
                if calls < required or (name == "host.frame" and calls != required) or metric.get("errors", 0):
                    problems.append(f"missing or partial {prefix}{name} for original four-cycle settle")
    # A timeout may leave partial drag work; report it, never fabricate 62.
    if selected and selected[0][0] == "pass":
        if count("renderer.dispatchMouseMove") != 62 or count("renderer.captureScreenshot") != 1:
            problems.append("successful original run missing moves or screenshot")
        if supervision.get("returncode") != 0:
            problems.append("pass line disagrees with original exit")
    if selected and selected[0][0] == "fail" and supervision.get("returncode") == 0:
        problems.append("failure line disagrees with original exit")
    if any(type(profile.get(key)) is not int or profile[key] <= 0 for key in ("mutations", "mutationBatches")):
        problems.append("no real mutation activity")
    return {"completeEvidence": not problems, "problems": problems,
            "originalResult": selected[0][0] if len(selected) == 1 else "unproved",
            "originalExit": supervision.get("returncode"),
            "originalTestTimeout": bool(re.search(r"(?:timed out|timeout).*30000", raw, re.IGNORECASE)),
            "supervisorDeadlineExceeded": supervision.get("deadlineExceeded"),
            "movesObserved": count("renderer.dispatchMouseMove"),
            "note": "Focused evidence cannot replace PR79's failed aggregate gate."}
