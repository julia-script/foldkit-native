"""Qualification evidence is separate from either original test's outcome."""
import hashlib
import json
import math
import re

BASE = "ee22edd496052d1df404d342052b24f66f9f6276"
CANDIDATE = "e7d106bd19df4bb378b418f29e7cb3624f6d5558"
CASES = {"baseline": BASE, "candidate": CANDIDATE}
TITLE = "a drag across a 32 × 32 board through GPUI: every cell it crosses, and how many moves a second"
FILTER = "Metal, offscreen.*a drag across a 32"
COMMAND = ["test", "--preload", "./test/support/dom.ts", "./examples/pixel-art/native.test.ts", "--test-name-pattern", FILTER]
DELTA = {"packages/foldkit-gpuix/src/host.ts", "packages/foldkit-gpuix/src/layout-invalidation-prototype.ts", "packages/foldkit-gpuix/test/layout-invalidation-prototype.test.ts"}
DRIVER = {".github/workflows/native-geometry-comparison.yml", *{"scripts/native-geometry-comparison/" + name for name in ("contract.py", "run.py", "supervise.py", "controls.py")}}
METHODS = ["nativeSimulateClick", "nativeSimulateMouseDown", "nativeSimulateMouseUp", "dispatchMouseMove", "flush", "dispatchNativeEvents", "getWindowSize", "getElementBounds", "getPaintedText", "getAutomationTree", "captureScreenshot"]
BLOCK_ENV = {"NAPI_RS_NATIVE_LIBRARY_PATH", "NAPI_RS_FORCE_WASI", "NODE_OPTIONS", "BUN_OPTIONS", "THROTTLE", "GPUIX_CPU_THROTTLE_APPLIED"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def finite(value, positive=False):
    return type(value) in (float, int) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def cleaned(state):
    return isinstance(state, dict) and state.get("started") is True and state.get("reaped") is True and state.get("groupClean") is True and state.get("deadlineExceeded") is False and type(state.get("returncode")) is int and finite(state.get("elapsedSeconds"), True)


def native_capability(cap, dependency):
    return isinstance(cap, dict) and cap.get("nativeAvailable") is True and cap.get("platform") == "darwin" and cap.get("arch") == "arm64" and cap.get("bun") == "1.3.9" and cap.get("modulePath") == dependency.get("testingPath") and cap.get("bindingPath") == dependency.get("bindingPath") and cap.get("methods") == {name: True for name in METHODS}


def observations(raw):
    raw = raw if isinstance(raw, str) else ""
    raw = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    lines = raw.splitlines()
    windows, timings = [], []
    for index, line in enumerate(lines):
        for prefix, out in [("native test window:", windows), ("pixel-art Metal drag:", timings)]:
            if prefix in line:
                try:
                    out.append({"line": index + 1, "value": json.loads(line.split(prefix, 1)[1].strip())})
                except ValueError:
                    out.append({"line": index + 1, "malformed": True})
    verdicts = re.findall(r"^\((pass|fail|skip)\) (.+)$", raw, re.MULTILINE)
    selected = [(status, title) for status, title in verdicts if TITLE in title]
    duration = None
    if len(selected) == 1:
        match = re.search(r"\[(\d+(?:\.\d+)?)(ms|s)\]$", selected[0][1])
        if match:
            duration = float(match[1]) * (1000 if match[2] == "s" else 1)
    return {"selected": selected, "otherExecuted": [v for v in verdicts if TITLE not in v[1] and v[0] != "skip"], "durationMs": duration, "windows": windows, "drag": timings, "original30000msTimeout": bool(re.search(r"(?:timed out|timeout).*30000", raw, re.I))}


def validate_case(name, case, expected_source, dependency, bun):
    problems = []
    if not isinstance(case, dict):
        case = {}
    state = case.get("supervision", {})
    state = state if isinstance(state, dict) else {}
    dependency = dependency if isinstance(dependency, dict) else {}
    result = observations(case.get("raw", ""))
    selected = result["selected"]
    outcome = selected[0][0] if len(selected) == 1 else "unproved"
    if case.get("sourceHead") != CASES[name] or case.get("executionKind") != "hosted-native":
        problems.append("source or genuine execution provenance mismatch")
    if not isinstance(expected_source, dict) or expected_source.get("head") != CASES[name]:
        problems.append("expected immutable source identity missing")
    if not re.fullmatch("[0-9a-f]{64}", str(dependency.get("bindingSha256", ""))) or not re.fullmatch("[0-9a-f]{64}", str(dependency.get("installedDigest", ""))):
        problems.append("exact dependency/native identity missing")
    if case.get("command") != [bun, *COMMAND]:
        problems.append("original focused command changed")
    environment = case.get("originalEnvironment")
    environment = environment if isinstance(environment, dict) else {}
    if state.get("command") != case.get("command") or environment.get("FOLDKIT_NATIVE_RENDERER") != "gpuix" or state.get("envelopeSeconds") != 60:
        problems.append("actual original command/renderer mismatch")
    if not cleaned(state):
        problems.append("original supervisor/owned cleanup incomplete")
    cap_state = case.get("capabilitySupervision")
    if not cleaned(cap_state) or cap_state.get("returncode") != 0 or cap_state.get("envelopeSeconds") != 15 or not native_capability(case.get("capability"), dependency):
        problems.append("native capability unproved")
    for phase in ("before", "after"):
        receipt = case.get(phase, {})
        receipt = receipt if isinstance(receipt, dict) else {}
        if receipt.get("source") != expected_source or receipt.get("dependencies") != dependency:
            problems.append(phase + " source/dependency/binding identity mismatch")
    if len(selected) != 1 or outcome not in ("pass", "fail") or result["otherExecuted"] or not finite(result["durationMs"], True):
        problems.append("one original non-skipped test result/duration required")
    original_exit = state.get("returncode")
    if outcome == "pass" and original_exit != 0 or outcome == "fail" and (type(original_exit) is not int or original_exit <= 0):
        problems.append("original outcome disagrees with raw exit")
    windows = result["windows"]
    if len(windows) != 1 or not isinstance(windows[0].get("value"), dict):
        problems.append("original real native window evidence missing")
    else:
        window = windows[0]["value"]
        if window.get("name") != "pixel-art" or any(not isinstance(window.get(size), dict) or any(not finite(window[size].get(axis), True) for axis in ("width", "height")) for size in ("requested", "actual")):
            problems.append("original window identity or geometry invalid")
    if outcome == "pass":
        drag = result["drag"]
        if len(drag) != 1 or not isinstance(drag[0].get("value"), dict) or drag[0]["value"].get("moves") != 62 or drag[0]["value"].get("cells") != 1024 or not finite(drag[0]["value"].get("ms"), True):
            problems.append("successful original drag timing evidence missing")
        png = case.get("png", {})
        png = png if isinstance(png, dict) else {}
        if png.get("validPng") is not True or not finite(png.get("width"), True) or not finite(png.get("height"), True) or not re.fullmatch("[0-9a-f]{64}", str(png.get("sha256", ""))):
            problems.append("successful original screenshot missing")
    return {"completeEvidence": not problems, "problems": problems, "originalResult": outcome, "originalExit": original_exit, "observations": result, "screenshot": case.get("png"), "note": "Original failure remains failure. Post-timeout logs/screenshots are not completed assertion proof."}


def validate_all(receipt, cases, expected, dependency, bun, cleanup):
    receipt, cases, expected, cleanup = [item if isinstance(item, dict) else {} for item in (receipt, cases, expected, cleanup)]
    problems = []
    reports = {name: validate_case(name, cases.get(name), expected.get(name), dependency, bun) for name in CASES}
    if receipt.get("executionKind") != "hosted-native" or receipt.get("platform") != "Darwin" or receipt.get("machine") != "arm64" or receipt.get("githubRepository") != "julia-script/foldkit-native" or not str(receipt.get("githubRunId", "")).isdigit() or receipt.get("githubHead") != receipt.get("driverHead"):
        problems.append("hosted provenance unavailable or synthetic")
    install = receipt.get("installSupervision")
    if receipt.get("bun") != "1.3.9" or receipt.get("node") != "v22.22.3" or not cleaned(install) or install.get("returncode") != 0 or install.get("command") != ["npm", "ci", "--ignore-scripts"]:
        problems.append("pinned runtime/owned installation unproved")
    if any(not r["completeEvidence"] for r in reports.values()):
        problems.append("separate original case evidence incomplete")
    sessions = []
    for name in CASES:
        case = cases.get(name)
        case = case if isinstance(case, dict) else {}
        state = case.get("supervision")
        state = state if isinstance(state, dict) else {}
        sessions.append(state.get("pgid"))
    if any(type(pid) is not int or pid <= 0 for pid in sessions) or len(set(sessions)) != 2:
        problems.append("fresh separate original sessions unproved")
    first, second = [cases.get(name) if isinstance(cases.get(name), dict) else {} for name in CASES]
    times = [first.get("originalStartedEpoch"), first.get("originalEndedEpoch"), second.get("originalStartedEpoch"), second.get("originalEndedEpoch")]
    if not all(finite(t, True) for t in times) or not times[0] <= times[1] <= times[2] <= times[3] or first.get("sourcePath") == second.get("sourcePath"):
        problems.append("serial isolated original sessions unproved")
    if receipt.get("driverSourceBefore") != receipt.get("driverSourceAfter") or not isinstance(receipt.get("driverSourceBefore"), dict) or receipt["driverSourceBefore"].get("head") != receipt.get("driverHead") or receipt.get("dependencyAfter") != dependency:
        problems.append("driver/shared dependency before-after identity mismatch")
    for name in CASES:
        if cleanup.get(name) is not True:
            problems.append("owned source worktree cleanup incomplete: " + name)
    complete = not problems
    return {"completeComparisonEvidence": complete, "problems": problems, "cases": reports, "candidateOriginalTestPassed": complete and reports["candidate"]["originalResult"] == "pass", "baselineOriginalTestPassed": complete and reports["baseline"]["originalResult"] == "pass", "performanceComparisonAvailable": complete and all(r["originalResult"] == "pass" for r in reports.values()), "note": "Focused native comparison is not production delivery or a timeout cause verdict."}


def exit_code(result):
    for name in CASES:
        value = result.get("cases", {}).get(name, {}).get("originalExit")
        if type(value) is int and value != 0:
            return value if 0 < value < 256 else 1
    return 0 if result.get("completeComparisonEvidence") else 1
