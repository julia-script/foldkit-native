"""Combined adapter qualification only; no aggregate or additional scenarios.

Original tracked fixtures remain unchanged. Every subprocess belongs to an
owned process group; result/source/binary evidence survives failures.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
EXPECTED = {
    "packages/foldkit-gpuix/src": "a9416e350352855beb99779eb2dadaa141f3614e",
    "src": "a31463f8c1827c0bcc480291176d6221a4c8caf4",
    "packages/foldkit-gpuix/test": "23e3fa5369ecdd9508f5d3b2b654e6ed5db46910",
    "test/support/windows.ts": "45e3b49840e0e8bd6c5075f47860cd5b07687e2e",
    "package-lock.json": "456982bbd587fbaa4087894eb56640129c55b9a4",
    "package.json": "05e0565d8b30b74285d8b6d062f3eeff0b3aa173",
    "packages/foldkit-gpuix/package.json": "9c289b3c835db970b04c1e0be740e84003698711",
    "scripts/wayland-session.sh": "7927ade9fa7d1ae73cacb7e047a41117e2501ba9"
}
PLATFORMS = {"macos": ("Darwin", "arm64", "darwin-arm64"),
             "linux": ("Linux", "x86_64", "linux-x64-gnu")}

def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def group_members(pgid):
    rows = subprocess.check_output(["ps", "-axo", "pid=,ppid=,pgid=,stat=,args="], text=True, timeout=5)
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
    with log.open("wb") as stream:
        child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                 stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        record.update(pid=child.pid, pgid=child.pid)
        # Ownership is established by Popen, even if interrupted before the
        # first ps sample. Later samples add the actual descendants.
        seen = {child.pid: {"pid": child.pid, "ppid": os.getpid(), "args": " ".join(map(str, argv))}}
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
    require(re.search(r"\b10 expect\(\) calls\b", log) is not None, "original macOS assertion count differs")


def nonempty(path):
    require(path.is_file() and path.stat().st_size > 0, f"missing output: {path}")


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
        require(all("596274" not in part for part in row["stale"]["text"]), "stale secret leaked")
        require("••••••" in row["stale"]["text"], "positive masked painted response missing")
        require("596274" in row["publicReuse"]["text"], "public reuse positive control missing")
        require(type(row["frameAtMutation"]) is int and row["frameAtMutation"] > previous,
                "native frames did not advance across controls")
        previous = row["frameAtMutation"]
    # The unchanged child additionally checks current replacement masking and
    # awaits native retirement before returning. Its successful exit is required.


def macos_child_receipt(out):
    files = list(out.glob("privacy-child-*.json"))
    require(len(files) == 1, "expected exactly one original native child")
    child = files[0]
    pid = int(child.stem.rsplit("-", 1)[-1])
    require(pid > 0 and child.with_suffix(".exit").read_text() == "0", "native child did not exit successfully")
    require(child.with_suffix(".stderr").is_file(), "native child stderr capture missing")
    record = json.loads((out / "tracked-privacy.json").read_text())
    require(record["status"] == "passed" and record["exit"] == 0 and not record["leaked"] and
            not record["survivors"], "tracked parent failed or owned processes survived")
    require(any(item["pid"] == pid for item in record["ownedProcesses"]), "native child is not in owned process evidence")



def validate_linux(root):
    import os
    from pathlib import Path
    import xml.etree.ElementTree as ET
    root = Path(root)
    report = ET.parse(root / 'junit.xml').getroot()
    cases = list(report.iter('testcase'))
    modes = ['default-disabled', 'opt-out', 'enabled']
    require(len(cases) == 3, 'Expected exactly three executed native modes')
    require(report.attrib.get('tests') == '3' and report.attrib.get('assertions') == '34' and
            report.attrib.get('failures') == '0' and report.attrib.get('skipped') == '0',
            'original Linux counts/skips/failures differ')
    require({case.attrib['name'] for case in cases} == {'linux retained privacy: ' + mode for mode in modes}, 'qualification guard failed')
    for case in cases:
        require(not any(case.find(tag) is not None for tag in ['skipped', 'failure', 'error']), 'Native mode did not pass')
        expected_count = '22' if case.attrib['name'].endswith(': enabled') else '6'
        require(case.attrib.get('assertions') == expected_count, 'native mode assertion count differs')
    require('Linux native window guard: true' in (root / 'guard.log').read_text(), 'qualification guard failed')
    require(list(root.glob('trace.*')), 'Missing owned process/cleanup evidence')
    require((root / 'session.exit').read_text().strip() == '0', 'native session failed')
    traces = {int(path.name.split('.')[-1]) for path in root.glob('trace.*')}
    cleanup = (root / 'cleanup.log').read_text().splitlines()
    require(all(re.fullmatch(r'owned traced PID exited: [1-9]\d*', line) for line in cleanup),
            'owned cleanup reported a survivor/invalid PID')
    require(traces == {int(line.split(': ')[-1]) for line in cleanup} and traces,
            'missing owned process cleanup records')
    require(not (root / 'target-status.log').read_text().strip(), 'target source changed')
    import json
    outputs = json.loads((root / 'outputs.json').read_text())
    require(any(item['name'] == 'HEADLESS-1' and item['active'] and
                item['current_mode']['width'] == 320 and item['current_mode']['height'] == 180
                for item in outputs), 'supported Wayland output missing')
    child_pids = set()
    for mode in modes:
        require((root / (mode + '.exit')).read_text() == '0', 'qualification guard failed')
        child_pid = int((root / (mode + '.child')).read_text())
        require(child_pid > 0 and child_pid in traces and child_pid not in child_pids, 'missing/duplicate native child trace')
        child_pids.add(child_pid)
        require((root / f'trace.{child_pid}').read_text().rstrip().endswith('+++ exited with 0 +++'),
                'native child trace has no successful exit')
        result = json.loads((root / (mode + '.stdout')).read_text())
        require(result['qualification'] == 'linux-retained-content' and result['mode'] == mode, 'qualification guard failed')
        rows = result['observations']
        phases = [row['phase'] for row in rows]
        require(len(rows) == (48 if mode == 'enabled' else 6) and len(set(phases)) == len(phases),
                'original observations missing/duplicated')
        diagnostics = [json.loads(line)['retainedPrivacyObservation']
                       for line in (root / (mode + '.stderr')).read_text().splitlines()
                       if line.startswith('{"retainedPrivacyObservation":')]
        require(rows == diagnostics, 'raw incremental diagnostics differ from child result')
        ordinary = next(row for row in rows if row['phase'] == 'ordinary-native-control')
        require('ORDINARY-CONTENT' in ordinary['raw'], 'Missing ordinary raw retained positive control')
        progress = [row for row in rows if 'before' in row and 'after' in row]
        require(len(progress) == (13 if mode == 'enabled' else 2), 'missing completed-frame observations')
        require(progress and all(isinstance(row['after'], int) and row['after'] > row['before'] for row in progress), 'No actual completed-frame progress')
        require(all(type(row['before']) is int and type(row['after']) is int and
                    0 <= row['before'] < row['after'] for row in progress), 'invalid completed-frame counters')
        require((root / (mode + '.stderr')).read_text(), 'Missing incremental raw diagnostics')
        if mode == 'enabled':
            for phase, secret in [('clear-initial-secret', '596274'), ('clear-stale-before-submit', '596274'),
                                  ('replace-initial-secret', '596274'), ('replace-stale-before-submit', '596274'),
                                  ('replacement-current-secret', '831905'), ('remove-initial-secret', '596274'),
                                  ('native-input-current-secret', '9'), ('rapid-current-newer-secret', 'NEWER-SECRET')]:
                row = next(row for row in rows if row['phase'] == phase)
                require(secret in row['raw'] and all(secret not in part for part in row['served']['text']),
                        'raw secret absent or SSE secret leaked')
                require('•' * len(secret) in row['served']['text'], 'positive masked SSE response missing')
                def inputs(node):
                    if node is None:
                        return []
                    return ([node] if node['type'] == 'input' else []) + [item for child in node.get('children', []) for item in inputs(child)]
                original = next(item for item in inputs(row['rawTree']) if item.get('text') == secret)
                for tree in (row['tree']['tree'], row['wireTree']):
                    served = next(item for item in inputs(tree) if item['id'] == original['id'])
                    require('text' not in served and 'value' not in served, 'SSE input text/value leaked')
                require('ORDINARY-CONTENT' in row['served']['text'], 'ordinary public SSE content lost')
                def ordinary_nodes(node):
                    if node is None:
                        return []
                    return ([node] if node.get('text') == 'ORDINARY-CONTENT' else []) + [item for child in node.get('children', []) for item in ordinary_nodes(child)]
                public = next(iter(ordinary_nodes(row['rawTree'])), None)
                require(public is not None and public['type'] not in ('input', 'textarea'), 'raw ordinary tree positive missing')
                for tree in (row['tree']['tree'], row['wireTree']):
                    require(any(item['id'] == public['id'] for item in ordinary_nodes(tree)), 'ordinary public tree content lost')
            for action in ['clear', 'replace', 'remove']:
                initial = next(row for row in rows if row['phase'] == action + '-initial-secret')
                require('596274' in initial['raw'] and '596274' not in initial['served']['text'], 'qualification guard failed')
                reuse = next(row for row in rows if row['phase'] == action + '-public-reuse')
                require('596274' in reuse['raw'] and '596274' in reuse['served']['text'], 'qualification guard failed')
            event = next(row for row in rows if row['phase'] == 'native-input-event')
            require(event['inputEvents'] > 0 and event['value'] == '9', 'Missing native input positive control')
            newer = next(row for row in rows if row['phase'] == 'rapid-current-newer-secret')
            require('NEWER-SECRET' in newer['raw'] and 'NEWER-SECRET' not in newer['served']['text'], 'qualification guard failed')
            reuse = next(row for row in rows if row['phase'] == 'rapid-public-reuse-current-protected')
            require('FIRST-CAPTURE' in reuse['raw'] and 'FIRST-CAPTURE' in reuse['served']['text'] and
                    'NEWER-SECRET' not in reuse['served']['text'], 'current/newer/public reuse control failed')
        else:
            disabled = next(row for row in rows if row['phase'] == mode)
            require('596274' in disabled['raw'] and disabled['listenersBefore'] == disabled['listenersAfter'], 'qualification guard failed')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(target, expected_head, kind):
    system, machine, suffix = PLATFORMS[kind]
    require((platform.system(), platform.machine()) == (system, machine), "unsupported native platform")
    require("FOLDKIT_NATIVE_NO_WINDOW" not in os.environ, "NO_WINDOW would skip native tests")
    require(os.environ.get("FOLDKIT_NATIVE_RENDERER", "gpuix") == "gpuix", "mirror is not native qualification")
    require(os.environ.get("NAPI_RS_ENFORCE_VERSION_CHECK") == "1", "native version guard disabled")
    require(os.environ.get("PYTHONDONTWRITEBYTECODE") == "1" and sys.dont_write_bytecode,
            "qualification must not generate unreviewed Python bytecode")
    def read(argv):
        return subprocess.check_output(argv, cwd=target, text=True, timeout=10).strip()
    head = read(["git", "rev-parse", "HEAD"])
    require(head == expected_head and re.fullmatch(r"[0-9a-f]{40}", head), "unexpected qualification HEAD")
    require(not read(["git", "status", "--porcelain"]), "qualification checkout changed")
    objects = {path: read(["git", "rev-parse", f"HEAD:{path}"]) for path in EXPECTED}
    require(objects == EXPECTED, "reviewed combined source/fixture/lock/session objects differ")
    versions = {tool: read([tool, "--version"]) for tool in ("node", "bun", "npm")}
    require(versions["node"] == "v22.22.3" and versions["bun"] == "1.3.9", "runtime pins differ")
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=target, timeout=10).decode().split("\0")
    tracked = {path: digest(target / path) for path in paths if path}
    deps = {}
    installed = target / "node_modules"
    dependency_files = {str(file.relative_to(installed)): digest(file)
                        for file in sorted(installed.rglob("*")) if file.is_file()}
    require(dependency_files, "installed dependency inventory empty")
    for name, expected in [("@gpuix/native", "0.10.0"), (f"@gpuix/native-{suffix}", "0.10.0"),
                           ("foldkit", "0.165.0"), ("effect", "4.0.0")]:
        path = target / "node_modules" / name / "package.json"
        version = json.loads(path.read_text())["version"]
        require(version == expected, f"unexpected {name} version")
        package = path.parent
        deps[name] = {"version": version, "sha256": digest(path),
                      "filesSha256": {str(file.relative_to(package)): digest(file)
                                      for file in sorted(package.rglob("*")) if file.is_file()}}
    binding = target / "node_modules" / f"@gpuix/native-{suffix}" / f"gpuix-native.{suffix}.node"
    nonempty(binding)
    return {"head": head, "objects": objects, "trackedSha256": tracked,
            "versions": versions, "dependencies": deps, "installedFilesSha256": dependency_files,
            "nativeBinary": {"path": str(binding), "bytes": binding.stat().st_size, "sha256": digest(binding)}}


def save_snapshot(target, out, kind, phase):
    data = snapshot(target, os.environ["QUALIFICATION_HEAD"], kind)
    (out / f"identity-{phase}.json").write_text(json.dumps(data, indent=2))
    if phase == "after":
        require(data == json.loads((out / "identity-before.json").read_text()),
                "source/dependency/native binary identity changed during qualification")
    return data


def macos(target, out):
    out.mkdir(parents=True, exist_ok=True)
    result = {"channel": "macos-painted", "head": os.environ.get("QUALIFICATION_HEAD"),
              "started": time.time(), "status": "failed"}
    env = dict(os.environ, QUALIFICATION_TARGET=str(target), QUALIFICATION_EVIDENCE=str(out),
               FOLDKIT_NATIVE_EVIDENCE=str(out), NO_COLOR="1", TERM="dumb")
    temporary = out / "tmp"
    temporary.mkdir(exist_ok=True)
    env["TMPDIR"] = str(temporary)
    try:
        require(not out.is_relative_to(target), "evidence must be outside checkout")
        save_snapshot(target, out, "macos", "before")
        command(["bun", "--eval", 'import { WINDOWS } from "./test/support/windows.ts"; if (process.platform !== "darwin" || !WINDOWS) throw new Error("Native window guard is false"); console.log("macOS native window guard: true")'],
                target, env, out, "guard", 10)
        log = command(["bun", "test", "--preload", str(HERE / "capture-privacy.mjs"),
                       "packages/foldkit-gpuix/test/automation-secrets-native.test.ts"],
                      target, env, out, "tracked-privacy", 115)
        test_passed(log, "native privacy: stale clear/replacement/removal and acknowledged public reuse")
        privacy_output(out)
        macos_child_receipt(out)
        result["status"] = "passed"
    except Exception as error:
        result["reason"] = str(error)
        print(f"combined macOS privacy failed: {error}", file=sys.stderr)
    finally:
        try:
            save_snapshot(target, out, "macos", "after")
        except Exception as error:
            result.update(status="failed", identityFailure=str(error))
        result["finished"] = time.time()
        (out / "result.json").write_text(json.dumps(result, indent=2))
    return 0 if result["status"] == "passed" else 1


def main():
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt("qualification interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    action, root, destination = sys.argv[1:]
    target, out = Path(root).resolve(), Path(destination).resolve()
    require(not out.is_relative_to(target), "evidence must be outside checkout")
    if action == "macos":
        return macos(target, out)
    if action == "validate-linux":
        validate_linux(out)
        return 0
    require(action in ("linux-before", "linux-after"), "unknown qualification action")
    out.mkdir(parents=True, exist_ok=True)
    save_snapshot(target, out, "linux", action.split("-")[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
