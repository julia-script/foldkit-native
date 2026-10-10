"""One unchanged native test, with an owned process group and raw exit retained.

The 60s process envelope only bounds a stuck child AFTER Bun's unchanged 30s
test deadline. It cannot turn a test timeout into success. No retries.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
sys.dont_write_bytecode = True
from validate import BASE, FILTER, validate


def group_members(pgid):
    rows = subprocess.check_output(["ps", "-axo", "pid=,pgid=,stat="], text=True)
    return [int(pid) for pid, group, state in (line.split() for line in rows.splitlines())
            if int(group) == pgid and not state.startswith("Z")]


def supervise(command, raw_path, env, deadline=60, state=None):
    started = time.monotonic()
    state = {} if state is None else state
    state.update(returncode=None, deadlineExceeded=False, reaped=False, groupClean=False,
                 started=False, signals=[], command=command, envelopeSeconds=deadline)
    with open(raw_path, "w") as output:
        child = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                 env=env, start_new_session=True)
        state.update(started=True, pid=child.pid, pgid=child.pid)
        exceeded = False
        signals = state["signals"]
        def send(sig):
            try:
                os.killpg(child.pid, sig)
                signals.append(signal.Signals(sig).name)
            except ProcessLookupError:
                pass  # Owned group already exited: the expected cleanup race.
            except PermissionError:
                # macOS can retain an orphaned zombie group under launchd:
                # signalling it yields EPERM rather than ESRCH. Only accept
                # that race after proving there are NO live owned members.
                if group_members(child.pid):
                    raise
                signals.append("group-has-no-live-members")
        try:
            try:
                child.wait(timeout=deadline)
            except subprocess.TimeoutExpired:
                exceeded = True
                state["deadlineExceeded"] = True
                send(signal.SIGTERM)
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    send(signal.SIGKILL)
                    child.wait(timeout=5)
        finally:
            # Includes grandchildren retained after the original child exits.
            try:
                send(signal.SIGKILL)
                child.wait(timeout=5)
            finally:
                # Preserve a known raw exit even if signalling/reaping fails.
                state.update(returncode=child.returncode, reaped=child.returncode is not None,
                             elapsedSeconds=time.monotonic() - started)
        cleanup_deadline = time.monotonic() + 2
        members = group_members(child.pid)
        while members and time.monotonic() < cleanup_deadline:
            time.sleep(0.02)
            members = group_members(child.pid)
    state.update({"returncode": child.returncode, "deadlineExceeded": exceeded,
            "reaped": child.poll() is not None, "groupClean": not members,
            "remainingGroupMembers": members, "signals": signals,
            "pid": child.pid, "pgid": child.pid, "envelopeSeconds": deadline,
            "elapsedSeconds": time.monotonic() - started, "command": command})
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    os.chdir(root)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    profile_path = out / "profile.json"
    raw_path = out / "original.log"
    raw_path.write_text("")
    profile_path.write_text(json.dumps({"stage": "unavailable", "reason": "no child profile available"}) + "\n")
    provenance = {"stage": "startup", "head": None, "base": BASE}
    supervision = {"started": False, "returncode": None, "deadlineExceeded": False,
                   "reaped": False, "groupClean": False}
    result = {"completeEvidence": False, "problems": ["startup incomplete"],
              "originalResult": "unproved", "originalExit": None}
    def write(name, value):
        (out / name).write_text(json.dumps(value, indent=2) + "\n")
    write("provenance.json", provenance)
    write("supervision.json", supervision)
    write("validation.json", result)
    write("error.json", {"stage": "startup", "failure": None})
    try:
        return execute(root, out, provenance, supervision, write)
    except Exception as error:
        failure = {"stage": provenance["stage"], "type": type(error).__name__, "message": str(error)[:2000]}
        provenance["failure"] = failure
        supervision["failure"] = failure
        result = {"completeEvidence": False, "problems": ["profile execution failed: " + failure["type"] + ": " + failure["message"]],
                  "originalResult": "unproved", "originalExit": supervision.get("returncode"),
                  "supervisorDeadlineExceeded": supervision.get("deadlineExceeded")}
        write("error.json", failure)
        write("provenance.json", provenance)
        write("supervision.json", supervision)
        write("validation.json", result)
        print(json.dumps(result))
        original = supervision.get("returncode")
        return original if isinstance(original, int) and original > 0 else 1


def execute(root, out, provenance, supervision, write):
    provenance["stage"] = "preflight"
    git = lambda *args: subprocess.check_output(["git", *args], text=True).strip()
    head = git("rev-parse", "HEAD")
    provenance["head"] = head
    git("merge-base", "--is-ancestor", BASE, head)
    changed = git("diff", "--name-only", BASE, head).splitlines()
    allowed = lambda name: name.startswith("scripts/pixel-art-drag-profile/") or name == ".github/workflows/pixel-art-drag-phase-profile.yml"
    if not changed or not all(allowed(name) for name in changed) or git("status", "--porcelain"):
        raise RuntimeError("profile requires a clean, additive-only committed tree")
    originals = ["examples/pixel-art/native.test.ts", "packages/foldkit-gpuix/test/support.ts", "examples/support/harness.ts", "package-lock.json", "bunfig.toml"]
    hashes = {}
    for name in originals:
        actual = (root / name).read_bytes()
        frozen = subprocess.check_output(["git", "show", f"{BASE}:{name}"])
        if actual != frozen:
            raise RuntimeError(f"frozen bytes changed: {name}")
        hashes[name] = hashlib.sha256(actual).hexdigest()
    bun = subprocess.check_output(["bun", "--version"], text=True).strip()
    node = subprocess.check_output(["node", "--version"], text=True).strip()
    if bun != "1.3.9" or node != "v22.22.3" or os.uname().sysname != "Darwin":
        raise RuntimeError("profile requires Bun1.3.9/Node22.22.3 on Darwin")
    profile_path = out / "profile.json"
    raw_path = out / "original.log"
    env = {**os.environ, "FOLDKIT_NATIVE_RENDERER": "gpuix", "FOLDKIT_NATIVE_EVIDENCE": str(out),
           "PIXEL_DRAG_PROFILE_HEAD": head, "PIXEL_DRAG_PROFILE_OUTPUT": str(profile_path)}
    command = ["bun", "test", "--preload", "./test/support/dom.ts", "--preload", "./scripts/pixel-art-drag-profile/preload.ts",
               "./examples/pixel-art/native.test.ts", "--test-name-pattern", FILTER]
    provenance.update(stage="supervision", head=head, base=BASE, bun=bun, node=node, frozenHashes=hashes, command=command)
    write("provenance.json", provenance)
    supervise(command, raw_path, env, state=supervision)
    write("supervision.json", supervision)
    provenance["stage"] = "validation"
    decode_problem = None
    try:
        profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}
        if not isinstance(profile, dict):
            raise ValueError("profile must be an object")
    except (OSError, ValueError) as error:
        profile = {}
        decode_problem = str(error)
    result = validate(raw_path.read_text(), profile, supervision, head)
    if decode_problem:
        result["completeEvidence"] = False
        result["problems"].append("unreadable profile: " + decode_problem)
    if result["originalResult"] == "pass":
        png = out / "pixel-art-big-stroke.png"
        if not png.exists() or png.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            result["completeEvidence"] = False
            result["problems"].append("successful run missing native screenshot")
    write("validation.json", result)
    provenance["stage"] = "finished"
    write("provenance.json", provenance)
    write("error.json", {"stage": "finished", "failure": None})
    print(json.dumps(result))
    original = supervision["returncode"]
    return original if original and original > 0 else (0 if original == 0 and result["completeEvidence"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
