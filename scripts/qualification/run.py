"""Bounded, exact-head native qualification; artifacts stay outside the target.

Only commands started in our process groups are inspected or terminated.
This is a focused gate, not an aggregate-suite or baseline-success claim.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import shutil
import subprocess
import sys
import time

HEADS = {
    "70": "e41853d099e96375912d43cb209bad4388ac5654",
    "71": "bc79903867ec1f6885a27e1f88ac4e956a30e7a5",
    "72": "fbdc40896b018cba6b81d1272c6aa3d7917296ae",
    "81": "7f448bf5d8590685b9c946e0c8f079f5c795c5e3",
}
LOCK = "010dbbba16a62bc9d29b5f5a45ff48d3ae534e2ea9c011820978101562bdb4be"
HERE = Path(__file__).resolve().parent
RESTART_SHA256 = "4f1652b8fff0228a3f62bf8d5314ee116e0fe0081d35942c625677a13bdb4ea2"


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def group_members(pgid):
    rows = subprocess.check_output(["ps", "-axo", "pid=,ppid=,pgid=,stat=,args="], text=True)
    members = []
    for row in rows.splitlines():
        values = row.strip().split(None, 4)
        if len(values) == 5 and int(values[2]) == pgid and not values[3].startswith("Z"):
            members.append({"pid": int(values[0]), "ppid": int(values[1]), "args": values[4]})
    return members


def signal_group(pgid, value):
    try:
        os.killpg(pgid, value)
    except ProcessLookupError:
        pass  # The owned group exited between inspection and signalling.


def command(argv, cwd, env, out, name, timeout, grace=3):
    log = out / f"{name}.log"
    record = {"argv": list(map(str, argv)), "cwd": str(cwd), "started": time.time(), "timeoutSeconds": timeout}
    (out / f"{name}.json").write_text(json.dumps({**record, "status": "starting"}, indent=2))
    with log.open("wb") as stream:
        try:
            child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                     stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        except Exception as error:
            (out / f"{name}.json").write_text(json.dumps({**record, "status": "spawn-failed", "reason": repr(error), "finished": time.time()}, indent=2))
            raise
        seen = {child.pid: {"pid": child.pid, "args": " ".join(map(str, argv))}}
        timed_out = False
        interrupted = None
        try:
            deadline = time.monotonic() + timeout
            while child.poll() is None:
                for member in group_members(child.pid):
                    seen[member["pid"]] = member
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.1)
        except BaseException as error:
            interrupted = error
        finally:
            deadline = time.monotonic() + grace
            while not timed_out and interrupted is None and group_members(child.pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            remaining = group_members(child.pid)
            if remaining:
                signal_group(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                deadline = time.monotonic() + 2
                while group_members(child.pid) and time.monotonic() < deadline:
                    time.sleep(0.1)
                if group_members(child.pid):
                    signal_group(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
            survivors = group_members(child.pid)
            status = ("interrupted" if interrupted else "timed-out" if timed_out else
                      "leaked-children" if remaining or survivors else "command-failed" if child.returncode else "passed")
            record.update(status=status, exit=child.returncode, finished=time.time(),
                          ownedProcesses=list(seen.values()), leaked=remaining, survivors=survivors)
            (out / f"{name}.json").write_text(json.dumps(record, indent=2))
        if interrupted:
            raise interrupted
    print(f"{name}: {status}, exit {child.returncode}", flush=True)
    require(status == "passed", f"{name}: {status}; see {log}")
    return log.read_text(errors="replace")


def test_passed(log, name):
    require(re.search(r"\b1 pass\b", log) is not None, "tracked fixture did not execute exactly one test")
    require(re.search(r"\b0 fail\b", log) is not None, "tracked fixture reported failures")
    require(not re.search(r"\b[1-9]\d* (skip|filtered|todo)\b|\(skip\)", log), "tracked fixture skipped or filtered")
    require(f"(pass) {name}" in log, "named tracked fixture did not pass")


def nonempty(path):
    require(path.is_file() and path.stat().st_size > 0, f"missing output: {path}")


def restart_source(target, out, stage):
    digest = hashlib.sha256((target / "examples/pixel-art/restart.test.ts").read_bytes()).hexdigest()
    (out / f"restart-source-{stage}.json").write_text(json.dumps({"head": HEADS["72"], "path": "examples/pixel-art/restart.test.ts", "sha256": digest}, indent=2))
    require(digest == RESTART_SHA256, "tracked restart fixture differs from source-reviewed locator repair")


def restart_output(log, out):
    require(re.search(r"\b17 expect\(\) calls\b", log), "original restart assertions did not all execute")
    match = re.search(r"Pixel Art restart: #262427 at the first start, (#[0-9a-fA-F]{6}) picked, (#[0-9a-fA-F]{6}) at the second start", log)
    require(match is not None and match[1] == match[2] and match[1] != "#262427", "missing positive second-launch colour restoration")
    screenshots = []
    for name in ("pixel-art-restart-1-picked.png", "pixel-art-restart-2-relaunched.png"):
        path = out / name
        nonempty(path)
        raw = path.read_bytes()
        require(raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) > 100, "missing native restart PNG")
        width, height = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
        require(width > 0 and height > 0, "empty native restart PNG dimensions")
        screenshots.append({"name": name, "bytes": len(raw), "width": width, "height": height, "sha256": hashlib.sha256(raw).hexdigest()})
    command_record = json.loads((out / "tracked-restart.json").read_text())
    launches = [item for item in command_record["ownedProcesses"] if "examples/open.ts pixel-art" in item["args"]]
    require(len({item["pid"] for item in launches}) == 2, "two actual owned native launches not observed")
    require(command_record["status"] == "passed" and command_record["exit"] == 0 and not command_record["leaked"] and not command_record["survivors"], "tracked restart command or owned cleanup failed")
    (out / "restart-acceptance.json").write_text(json.dumps({"head": HEADS["72"], "fixtureSha256": RESTART_SHA256, "expects": 17, "picked": match[1], "restored": match[2], "launches": launches, "screenshots": screenshots}, indent=2))


def cart_inputs(out):
    inputs = json.loads((out / "cart-inputs.json").read_text())
    delivered = [row for row in inputs if row["kind"] == "delivered"]
    observed = [row for row in inputs if row["kind"] == "observed"]
    expected = ["Checkout", "Products"]
    for order in range(1, 4):
        expected += ["Add to Cart", "Checkout", "Place Order"]
        if order < 3:
            expected += ["Continue Shopping" if order == 1 else "Products", "Checkout", "Products"]
    require([row["label"] for row in delivered] == expected and
            [(row["label"], row["id"], row["point"]) for row in observed] ==
            [(row["label"], row["id"], row["point"]) for row in delivered], "missing native Cart delivery/state controls")
    for row in delivered:
        x, y = row["point"]["x"], row["point"]["y"]
        target, viewport = row["bounds"], row["viewport"]
        require(0 < x < viewport["width"] and 0 < y < viewport["height"] and
                target["x"] < x < target["x"] + target["width"] and
                target["y"] < y < target["y"] + target["height"], "out-of-window/target Cart input")
    for row in observed:
        require(row["before"]["text"] != row["after"]["text"] and row["after"]["painted"], "missing native Cart observable state change")
    frames = json.loads((out / "cart-frame-measurements.json").read_text())
    require(len(frames) >= len(delivered), "missing measured native frame controls")
    for frame in frames:
        nonempty(Path(frame["path"]))


def privacy_output(out):
    files = list(out.glob("privacy-child-*.json"))
    require(len(files) == 1, "expected one captured original privacy child")
    result = json.loads(files[0].read_text())
    require(result["platform"] == "darwin", "privacy painted fixture is macOS-only")
    rows = result["nativePrivacy"]
    require([row["action"] for row in rows] == ["clear", "replace", "remove"], "missing privacy controls")
    previous = 0
    for row in rows:
        require("596274" in row["rawAtMutation"], "raw secret warmup/timing positive control missing")
        require("596274" not in row["stale"]["text"], "stale secret leaked")
        require("596274" in row["publicReuse"]["text"], "public reuse positive control missing")
        require(row["frameAtMutation"] > previous, "native frames did not advance across controls")
        previous = row["frameAtMutation"]
    # The unchanged child additionally checks current replacement masking and
    # awaits native retirement before returning. Its successful exit is required.


def theme_states(raw, report=None):
    size = 140 * 180 * 3
    require(len(raw) >= size * 6 and len(raw) % size == 0, "missing decoded recording frames")
    colors = {"dark": [(20, 19, 24), (29, 28, 35)], "light": [(244, 241, 234), (255, 253, 248)]}
    states = []
    for start in range(0, len(raw), size):
        frame = raw[start:start + size]
        counts = {state: 0 for state in colors}
        for i in range(0, size, 3):
            for state, tokens in colors.items():
                if any(all(abs(frame[i + k] - rgb[k]) <= 8 for k in range(3)) for rgb in tokens):
                    counts[state] += 1
        state = next((key for key in colors if counts[key] > 0.10 * size / 3 and counts["light" if key == "dark" else "dark"] < 0.02 * size / 3), "unknown")
        states.append({"seconds": start / size / 4, "state": state, "pixels": counts})
    runs = []
    for index, row in enumerate(states):
        if row["state"] == "unknown":
            continue
        if not runs or runs[-1]["state"] != row["state"]:
            runs.append({"state": row["state"], "indices": []})
        runs[-1]["indices"].append(index)
    if report is not None:
        report.write_text(json.dumps({"samples": states, "runs": runs, "tolerance": 8, "source": "packages/ui/src/theme.ts dusk/paper canvas/surface tokens"}, indent=2))
    require(len(runs) >= 3 and [row["state"] for row in runs[:3]] == ["dark", "light", "dark"], "clip did not visibly transition dark/light/dark")
    require(all(len(row["indices"]) >= 2 for row in runs[:3]), "theme state lacks two captured frames")
    return states, [row["indices"][len(row["indices"]) // 2] / 4 for row in runs[:3]]


def qualify(case, target, out):
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    require(platform.system() == "Darwin" and platform.machine() == "arm64", "requires supported macOS arm64 hosted runner")
    require("FOLDKIT_NATIVE_NO_WINDOW" not in env, "NO_WINDOW would skip native tests")
    require(env.get("FOLDKIT_NATIVE_RENDERER", "gpuix") == "gpuix", "mirror is not a native qualification")
    env.update(QUALIFICATION_TARGET=str(target), QUALIFICATION_EVIDENCE=str(out), FOLDKIT_NATIVE_EVIDENCE=str(out), FOLDKIT_NATIVE_RENDERER="gpuix", NO_COLOR="1", TERM="dumb")
    temporary = out / "tmp"
    temporary.mkdir()
    env["TMPDIR"] = str(temporary)
    def run(argv, name, timeout):
        return command(argv, target, env, out, name, timeout)
    head = run(["git", "rev-parse", "HEAD"], "head-before", 10).strip()
    require(head == HEADS[case], "target HEAD differs from reviewed immutable SHA")
    require(not run(["git", "status", "--porcelain", "--untracked-files=no"], "status-before", 10).strip(), "target has tracked changes")
    require(hashlib.sha256((target / "package-lock.json").read_bytes()).hexdigest() == LOCK, "unexpected lockfile")
    restart_source(target, out, "before")
    driver = run(["git", "-C", str(HERE.parent.parent), "rev-parse", "HEAD"], "driver-head", 10).strip()
    require(driver == env.get("GITHUB_SHA"), "driver differs from the reviewed push head")
    require(not run(["git", "-C", str(HERE.parent.parent), "status", "--porcelain"], "driver-status", 10).strip(), "driver has source changes")
    provenance = {"head": head, "case": case, "driverHead": os.environ.get("GITHUB_SHA"), "platform": platform.platform(), "machine": platform.machine(), "lockSha256": LOCK, "toolPaths": {tool: shutil.which(tool) for tool in ("node", "bun", "npm")}}
    for tool in ("node", "bun", "npm"):
        provenance[tool] = run([tool, "--version"], f"{tool}-version", 10).strip()
    require(provenance["node"] == "v22.22.3" and provenance["bun"] == "1.3.9", "runtime pins differ")
    (out / "provenance.json").write_text(json.dumps(provenance, indent=2))
    run(["sysctl", "hw.memsize", "hw.ncpu", "vm.loadavg", "vm.swapusage"], "host-resources", 10)
    run(["vm_stat"], "host-memory", 10)
    run(["npm", "ci"], "npm-ci", 180)
    run(["npm", "ls", "--depth=0"], "dependencies", 20)
    if case == "70":
        # Compile a read-only AppKit/CoreGraphics metadata probe outside both
        # checkouts. It opens no window and never sends input to production.
        probe = out / "macos-frame"
        run(["swiftc", str(HERE / "macos-frame.swift"), "-o", str(probe)], "frame-probe-compile", 60)
        env["QUALIFICATION_FRAME_PROBE"] = str(probe)
        run(["bun", str(HERE / "cart.ts")], "native-cart", 90)
        rows = json.loads((out / "cart-observations.json").read_text())
        required = ["empty-checkout", "first-instructions"] + [f"order-{i}-{stage}" for i in range(1, 4) for stage in ("ready", "success")] + [f"order-{i}-confirmation-retained" for i in (1, 2)]
        require([row["label"] for row in rows] == ["empty-checkout", "order-1-ready", "first-instructions", "order-1-success", "order-1-confirmation-retained", "order-2-ready", "order-2-success", "order-2-confirmation-retained", "order-3-ready", "order-3-success"], "incomplete native Cart observations")
        for label in required:
            nonempty(out / f"{label}.png")
        cart_inputs(out)
    elif case == "71":
        run(["brew", "install", "ffmpeg"], "ffmpeg-install", 300)
        run(["ffmpeg", "-version"], "ffmpeg-version", 10)
        run(["ffprobe", "-version"], "ffprobe-version", 10)
        encoders = run(["ffmpeg", "-hide_banner", "-encoders"], "ffmpeg-encoders", 10)
        require("libx264" in encoders, "required libx264 encoder missing")
        log = run(["bun", "run", "record", "native-ui", str(out)], "native-recording", 90)
        require(re.search(r"[1-9]\d* frames over [\d.]+ s", log), "recorder did not report captured native frames")
        clip = out / "native-ui-adapter.mp4"
        nonempty(clip)
        nonempty(out / "native-ui-adapter.png")
        probe = json.loads(run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(clip)], "clip-probe", 20))
        video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
        require(video["codec_name"] == "h264" and int(video["nb_read_frames"]) > 1 and float(probe["format"]["duration"]) > 0, "invalid recording stream")
        decoded = out / "clip.rgb"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-vf", "fps=4,scale=140:180", "-pix_fmt", "rgb24", "-f", "rawvideo", str(decoded)], "clip-decode", 30)
        _, times = theme_states(decoded.read_bytes(), out / "theme-transitions.json")
        for state, at in zip(("dark-before", "light", "dark-after"), times):
            run(["ffmpeg", "-y", "-v", "error", "-ss", str(at), "-i", str(clip), "-frames:v", "1", str(out / f"theme-{state}.png")], f"frame-{state}", 20)
            nonempty(out / f"theme-{state}.png")
        (out / "selected-theme-frames.json").write_text(json.dumps(dict(zip(("dark-before", "light", "dark-after"), times)), indent=2))
        decoded.unlink()
    elif case == "72":
        name = "Pixel Art, native window, two launches > a colour and empty canvas size picked before the process ended are restored when it starts again"
        log = run(["bun", "test", "examples/pixel-art/restart.test.ts"], "tracked-restart", 75)
        test_passed(log, name)
        require("Pixel Art restart: #262427 at the first start," in log, "missing actual restart fixture output")
        for name in ("pixel-art-restart-1-picked.png", "pixel-art-restart-2-relaunched.png"):
            nonempty(out / name)
        restart_output(log, out)
    else:
        log = run(["bun", "test", "--preload", str(HERE / "capture-privacy.mjs"), "packages/foldkit-gpuix/test/automation-secrets-native.test.ts"], "tracked-privacy", 115)
        test_passed(log, "native privacy: stale clear/replacement/removal and acknowledged public reuse")
        privacy_output(out)
def main():
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt("qualification interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    case, root, destination = sys.argv[1:]
    require(case == "72", "this dedicated runner only permits PR72")
    target, out = Path(root).resolve(), Path(destination).resolve()
    require(not out.is_relative_to(target), "evidence must be outside target checkout")
    result = {"case": case, "targetHead": HEADS[case], "started": time.time(), "status": "failed"}
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2))
    try:
        qualify(case, target, out)
        result["status"] = "passed"
    except BaseException as error:
        result["reason"] = str(error)
        print(f"PR{case}: FAILED: {error}", file=sys.stderr)
    finally:
        out.mkdir(parents=True, exist_ok=True)
        # Preserve revision/source checks even when a scenario failed.
        try:
            head = command(["git", "rev-parse", "HEAD"], target, dict(os.environ), out, "head-after", 10).strip()
            status = command(["git", "status", "--porcelain", "--untracked-files=no"], target, dict(os.environ), out, "status-after", 10).strip()
            require(head == HEADS[case] and not status, "target revision/source changed")
            restart_source(target, out, "after")
        except Exception as error:
            result["status"] = "failed"
            result["sourceCheckFailure"] = str(error)
        result["finished"] = time.time()
        (out / "result.json").write_text(json.dumps(result, indent=2))
        files = {str(path.relative_to(out)): hashlib.sha256(path.read_bytes()).hexdigest() for path in out.rglob("*") if path.is_file() and path.name != "manifest.json"}
        (out / "manifest.json").write_text(json.dumps(files, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
