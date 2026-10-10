"""LOCAL qualification preparation: no test/helper/renderer instrumentation.

Baseline and candidate run serially in fresh process groups, sharing one exact
installed dependency tree. A failing original test never becomes successful.
"""
import argparse
import hashlib
import io
import json
import os
import signal
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import zlib

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
# Standard-library-only bootstrap must precede every local-module import.
BOOTSTRAP_CASES = {
    'baseline_pixel32': '56d0f3727e0cb230c98d16977e58e24cc4a215c6',
    'candidate_pixel32': '30ef3d00f310634f030d26e49acbe2bfeae7ce5a',
}


def load_runtime():
    import contract
    import supervise as supervisor
    if {name: spec['head'] for name, spec in contract.CASES.items()} != BOOTSTRAP_CASES:
        raise RuntimeError('bootstrap immutable case contract differs')
    for name in ('BASE', 'CANDIDATE', 'MERGE', 'CASES', 'SPECS', 'command_for', 'capability_command', 'DELTA', 'DRIVER', 'METHODS', 'BLOCK_ENV', 'digest', 'validate_all', 'exit_code'):
        globals()[name] = getattr(contract, name)
    globals()['supervise'] = supervisor.supervise
    return supervisor


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, timeout=20).strip()


def expected_source(root, head):
    archive = subprocess.check_output(["git", "-C", str(root), "archive", head], timeout=20)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
        files = {entry.name: hashlib.sha256(tree.extractfile(entry).read()).hexdigest() for entry in tree if entry.isfile()}
    return {"head": head, "files": files, "digest": digest(files)}


def source_identity(root, expected):
    if git(root, "rev-parse", "HEAD") != expected["head"] or git(root, "status", "--porcelain"):
        raise RuntimeError("source head or clean tree changed: " + str(root))
    actual = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in expected["files"]}
    if actual != expected["files"]:
        raise RuntimeError("tracked source bytes differ from immutable commit")
    return {"head": expected["head"], "files": actual, "digest": digest(actual)}


def dependencies(root, bun, node):
    store = (root / "node_modules").resolve(strict=True)
    native = store / "@gpuix/native"
    package = store / "@gpuix/native-darwin-arm64"
    binding = package / "gpuix-native.darwin-arm64.node"
    pins = {}
    for name, version in [("@gpuix/native", "0.10.0"), ("@gpuix/native-darwin-arm64", "0.10.0"), ("foldkit", "0.165.0"), ("effect", "4.0.0")]:
        pins[name] = json.loads((store / name / "package.json").read_text())["version"]
        if pins[name] != version:
            raise RuntimeError("dependency pin mismatch: " + name)
    if list(native.rglob("*.node")) or binding.read_bytes()[:4] != b"\xcf\xfa\xed\xfe":
        raise RuntimeError("unexpected local binding or non-Mach-O native package")
    installed = {str(path.relative_to(store)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(store.rglob("*")) if path.is_file()}
    return {"pins": pins, "installedDigest": digest(installed), "installedFiles": len(installed), "bindingPath": str(binding.resolve()), "bindingSha256": hashlib.sha256(binding.read_bytes()).hexdigest(), "testingPath": str((native / "dist/testing.js").resolve()), "bunPath": bun, "bunSha256": hashlib.sha256(Path(bun).read_bytes()).hexdigest(), "nodePath": node, "nodeSha256": hashlib.sha256(Path(node).read_bytes()).hexdigest()}


def png_receipt(path):
    if not path.exists():
        return {"available": False}
    data = path.read_bytes()
    valid, width, height = False, 0, 0
    try:
        if len(data) > 32 * 1024 * 1024 or data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("missing PNG signature or oversized screenshot")
        at, payload, chunks, ended = 8, bytearray(), [], False
        while at < len(data):
            length = struct.unpack(">I", data[at:at + 4])[0]
            kind, part = data[at + 4:at + 8], data[at + 8:at + 8 + length]
            crc = struct.unpack(">I", data[at + 8 + length:at + 12 + length])[0]
            if zlib.crc32(kind + part) != crc:
                raise ValueError("PNG chunk CRC mismatch")
            chunks.append(kind)
            at += 12 + length
            if kind == b"IHDR":
                width, height, bits, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", part)
                if not 0 < width <= 8192 or not 0 < height <= 8192 or bits != 8 or color not in (2, 6) or (compression, filtering, interlace) != (0, 0, 0):
                    raise ValueError("unsupported native screenshot format")
            elif kind == b"IDAT":
                payload.extend(part)
            elif kind == b"IEND":
                ended = length == 0 and at == len(data)
                break
        if chunks.count(b"IHDR") != 1 or chunks[0] != b"IHDR" or not ended or not payload:
            raise ValueError("incomplete PNG structure")
        row = width * (4 if color == 6 else 3) + 1
        stream = zlib.decompressobj()
        pixels = stream.decompress(payload, row * height + 1)
        valid = len(pixels) == row * height and stream.eof and not stream.unused_data and not stream.unconsumed_tail and all(pixels[y * row] in range(5) for y in range(height))
    except (ValueError, struct.error, zlib.error, IndexError):
        pass
    return {"available": True, "validPng": valid, "width": width, "height": height, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "note": "Original screenshot completeness checked; no extra native capture or Ink/pixel assertions."}


def seed(out):
    out.mkdir(parents=True, exist_ok=False)
    receipt = {"stage": "startup", "executionKind": "unproved", "driverHead": None, "driverPorcelain": None,
               "githubHead": os.environ.get("GITHUB_SHA"), "githubRepository": os.environ.get("GITHUB_REPOSITORY"),
               "githubRunId": os.environ.get("GITHUB_RUN_ID"), "platform": platform.system(), "machine": platform.machine(),
               "python": {"executable": sys.executable, "version": sys.version, "cachePrefix": getattr(sys, "pycache_prefix", None),
                          "bytecodeDisabled": sys.dont_write_bytecode, "inheritedBytecodePolicy": os.environ.get("PYTHONDONTWRITEBYTECODE")}}
    cases = {}
    for name, head in BOOTSTRAP_CASES.items():
        folder = out / name
        folder.mkdir()
        for file in ("original.log", "capability.log", "prepare.log", "cleanup.log"):
            (folder / file).write_text("")
        cases[name] = {"name": name, "sourceHead": head, "executionKind": "unproved", "raw": "", "supervision": {"started": False, "returncode": None, "reaped": False, "groupClean": False}, "capabilitySupervision": {"started": False, "returncode": None}}
        write(folder / "case.json", cases[name])
    (out / "install.log").write_text("")
    write(out / "provenance.json", receipt)
    write(out / "error.json", {"stage": "startup", "failure": None})
    write(out / "cleanup.json", {})
    write(out / "comparison.json", {"completeComparisonEvidence": False, "problems": ["startup incomplete"], "cases": {name: {"originalResult": "unproved", "originalExit": None} for name in BOOTSTRAP_CASES}})
    return receipt, cases


def run_case(root, folder, case, expected, dependency, bun, env):
    if Path.cwd().resolve() != root.resolve():
        raise RuntimeError("original child source cwd mismatch")
    case.update(executionKind="hosted-native", sourcePath=str(root), command=command_for(case["name"], bun), originalEnvironment={name: env[name] for name in ("FOLDKIT_NATIVE_RENDERER", "FOLDKIT_NATIVE_EVIDENCE")}, before={"source": source_identity(root, expected), "dependencies": dependencies(root, bun, dependency["nodePath"])})
    cap = case["capabilitySupervision"]
    try:
        supervise(capability_command(bun), folder / "capability.log", env, deadline=15, state=cap)
        prefix = "history-grid-qualification-capability:"
        records = [line[len(prefix):] for line in (folder / "capability.log").read_text().splitlines() if line.startswith(prefix)]
        if len(records) != 1:
            raise RuntimeError("one explicit native capability receipt required")
        case["capability"] = json.loads(records[0])
        from contract import cleaned, native_capability
        if not cleaned(cap) or cap["returncode"] != 0 or not native_capability(case["capability"], dependency):
            raise RuntimeError("real native capability rejected")
        case["originalStartedEpoch"] = time.time()
        try:
            supervise(case["command"], folder / "original.log", env, deadline=60, state=case["supervision"])
        finally:
            case["originalEndedEpoch"] = time.time()
    finally:
        case["raw"] = (folder / "original.log").read_text()
        case["screenshots"] = {name: png_receipt(folder / name) for name in SPECS[CASES[case["name"]]["test"]]["screenshots"]}
        try:
            case["after"] = {"source": source_identity(root, expected), "dependencies": dependencies(root, bun, dependency["nodePath"])}
        finally:
            write(folder / "case.json", case)


def execute(root, out, receipt, cases, expected, owned):
    receipt["stage"] = "preflight"
    head = git(root, "rev-parse", "HEAD")
    receipt["driverHead"] = head
    receipt["driverPorcelain"] = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"], text=True, timeout=20)
    driver = expected_source(root, head)
    source_identity(root, driver)
    if git(root, "merge-base", BASE, head) != BASE:
        raise RuntimeError("driver must descend from immutable baseline")
    changed = set(git(root, "diff", "--name-only", MERGE, head).splitlines())
    added = set(git(root, "diff", "--diff-filter=A", "--name-only", MERGE, head).splitlines())
    if changed != DRIVER or added != DRIVER:
        raise RuntimeError("driver must be exactly the six additive reviewed files above the production merge")
    if set(git(root, "diff", "--name-only", BASE, CANDIDATE).splitlines()) != DELTA:
        raise RuntimeError("candidate source delta changed")
    candidate_tree = git(root, 'rev-parse', CANDIDATE + '^{tree}')
    merge_tree = git(root, 'rev-parse', MERGE + '^{tree}')
    if candidate_tree != merge_tree:
        raise RuntimeError('reviewed candidate differs from production merge tree')
    receipt['candidateMergeTree'] = {'candidate': CANDIDATE, 'merge': MERGE, 'candidateTree': candidate_tree, 'mergeTree': merge_tree}
    expected.update({name: expected_source(root, spec["head"]) for name, spec in CASES.items()})
    for name, sha in expected["baseline_pixel32"]["files"].items():
        if name not in DELTA and expected["candidate_pixel32"]["files"].get(name) != sha:
            raise RuntimeError("frozen original source changed: " + name)
    receipt.update(driverSourceBefore=driver, platform=platform.system(), machine=platform.machine(), githubRepository=os.environ.get("GITHUB_REPOSITORY"), githubRunId=os.environ.get("GITHUB_RUN_ID"), githubHead=os.environ.get("GITHUB_SHA"))
    if receipt["platform"] != "Darwin" or receipt["machine"] != "arm64" or os.environ.get("GITHUB_ACTIONS") != "true" or receipt["githubRepository"] != "julia-script/foldkit-native" or receipt["githubHead"] != head or not str(receipt["githubRunId"]).isdigit():
        raise RuntimeError("requires the released exact-head hosted Darwin arm64 job")
    if any(os.environ.get(name) for name in BLOCK_ENV) or any(name.startswith("PIXEL_DRAG_PROFILE") for name in os.environ):
        raise RuntimeError("native loader/throttle/preload environment override rejected")
    bun, node = str(Path(shutil.which("bun")).resolve()), str(Path(shutil.which("node")).resolve())
    receipt.update(bun=subprocess.check_output([bun, "--version"], text=True, timeout=10).strip(), node=subprocess.check_output([node, "--version"], text=True, timeout=10).strip())
    if receipt["bun"] != "1.3.9" or receipt["node"] != "v22.22.3":
        raise RuntimeError("official runtime pins required")
    env = {**os.environ, "FOLDKIT_NATIVE_RENDERER": "gpuix"}
    receipt.update(stage="install", executionKind="hosted-native")
    receipt["installSupervision"] = {}
    supervise(["npm", "ci", "--ignore-scripts"], out / "install.log", env, deadline=180, state=receipt["installSupervision"])
    from contract import cleaned
    if not cleaned(receipt["installSupervision"]) or receipt["installSupervision"]["returncode"] != 0:
        raise RuntimeError("exact dependency installation failed")
    dependency = dependencies(root, bun, node)
    receipt["dependencyIdentity"] = dependency
    receipt["sourceDelta"] = {"base": BASE, "candidate": CANDIDATE, "files": sorted(DELTA)}
    scratch = root / ".scratch"
    scratch.mkdir(exist_ok=True)
    if scratch.is_symlink() or scratch.resolve().parent != root.resolve():
        raise RuntimeError("scratch ownership boundary rejected")
    owner = Path(tempfile.mkdtemp(prefix="history-grid-qualification-", dir=scratch))
    receipt["ownedDirectory"] = str(owner)
    for name, spec in CASES.items():
        sha = spec["head"]
        folder = out / name
        source = owner / name
        owned[name] = source
        receipt["stage"] = name + ":prepare"
        state = {}
        cases[name]["prepareSupervision"] = state
        supervise(["git", "-C", str(root), "worktree", "add", "--detach", str(source), sha], folder / "prepare.log", env, deadline=20, state=state)
        if not cleaned(state) or state["returncode"] != 0:
            raise RuntimeError("owned source worktree preparation failed")
        (source / "node_modules").symlink_to((root / "node_modules").resolve(), target_is_directory=True)
        receipt["stage"] = name + ":native"
        write(out / "provenance.json", receipt)
        # Each case's fresh child cwd is implicit to avoid altering its command.
        before = Path.cwd()
        try:
            os.chdir(source)
            run_case(source, folder, cases[name], expected[name], dependency, bun, {**env, "FOLDKIT_NATIVE_EVIDENCE": str(folder)})
        finally:
            os.chdir(before)
        if not cleaned(cases[name]["supervision"]):
            raise RuntimeError("original child cleanup incomplete; stop before next case")
    receipt["driverSourceAfter"] = source_identity(root, driver)
    receipt["dependencyAfter"] = dependencies(root, bun, node)
    if receipt["dependencyAfter"] != dependency:
        raise RuntimeError("shared native/runtime identity changed")
    return dependency, bun


def cleanup_sources(root, out, owned, cases):
    cleanup = {}
    for name, path in owned.items():
        if name not in CASES or path.name != name or path.parent.parent.resolve() != (root / ".scratch").resolve() or not path.parent.name.startswith("history-grid-qualification-") or path.is_symlink():
            cleanup[name] = False
            cases[name]["cleanupFailure"] = {"type": "OwnershipRejected", "message": "refusing unowned source removal"}
            continue
        state = cases[name].get("supervision", {})
        cap = cases[name].get("capabilitySupervision", {})
        if any(s.get("started") and not s.get("groupClean") for s in (state, cap, cases[name].get("prepareSupervision", {}))):
            cleanup[name] = False
            continue
        try:
            registered = "worktree " + str(path) in git(root, "worktree", "list", "--porcelain").splitlines()
            if path.exists() or registered:
                result = subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10)
                (out / name / "cleanup.log").write_text(result.stdout)
                remaining = "worktree " + str(path) in git(root, "worktree", "list", "--porcelain").splitlines()
                cleanup[name] = result.returncode == 0 and not path.exists() and not remaining
            else:
                cleanup[name] = True
        except BaseException as error:
            cleanup[name] = False
            cases[name]["cleanupFailure"] = {"type": type(error).__name__, "message": str(error)[:2000]}
    if owned and all(cleanup.values()):
        owners = {path.parent for path in owned.values()}
        for owner in owners:
            try:
                owner.rmdir()
            except OSError as error:
                cleanup["ownerDirectory"] = False
                for name in owned:
                    cases[name]["cleanupFailure"] = {"type": type(error).__name__, "message": str(error)[:2000]}
    return cleanup


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    out = Path(args.out).resolve()
    if out == root or (out.is_relative_to(root) and out.parts[len(root.parts)] != "evidence"):
        raise RuntimeError("output must be outside source or inside ignored evidence only")
    receipt, cases = seed(out)
    expected, owned, cleanup = {}, {}, {}
    dependency, bun = {}, "unproved"
    failures = []
    interrupted = None
    pending = []
    def stop(signum, _frame):
        if not pending:
            pending.append(signum)
            error = KeyboardInterrupt('entrypoint cancelled by ' + signal.Signals(signum).name)
            error.signum = signum
            raise error
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        receipt["stage"] = "runtime-import"
        supervisor = load_runtime()
        os.environ["HISTORY_GRID_QUALIFICATION_OWNERSHIP"] = str(out / "ownership-registry")
        with supervisor.cancellation():
            dependency, bun = execute(root, out, receipt, cases, expected, owned)
    except BaseException as error:
        failures.append({"stage": receipt["stage"], "type": type(error).__name__, "message": str(error)[:2000]})
        interrupted = getattr(error, "signum", None)
        if interrupted and not pending:
            pending.append(interrupted)
    finally:
        try:
            cleanup = cleanup_sources(root, out, owned, cases)
            for name, case in cases.items():
                if case.get("cleanupFailure"):
                    failures.append({"stage": "cleanup:" + name, **case["cleanupFailure"]})
        except BaseException as error:
            failures.append({"stage": "cleanup", "type": type(error).__name__, "message": str(error)[:2000]})
        for name in BOOTSTRAP_CASES:
            write(out / name / "case.json", cases[name])
        try:
            result = validate_all(receipt, cases, expected, dependency, bun, cleanup)
        except BaseException as error:
            failures.append({"stage": "validation", "type": type(error).__name__, "message": str(error)[:2000]})
            result = {"completeComparisonEvidence": False, "candidateOriginalTestsPassed": False, "baselineOriginalTestsPassed": False, "allOriginalTestsPassed": False, "problems": ["validation failed"], "cases": {name: {"originalResult": "unproved", "originalExit": cases[name].get("supervision", {}).get("returncode")} for name in BOOTSTRAP_CASES}}
        if failures:
            result["completeComparisonEvidence"] = False
            result["candidateOriginalTestsPassed"] = False
            result["baselineOriginalTestsPassed"] = False
            result["allOriginalTestsPassed"] = False
            result["problems"].extend(failures)
        receipt.update(stage="finished", failures=failures, cancelledBy=interrupted)
        write(out / "provenance.json", receipt)
        write(out / "error.json", {"failures": failures})
        write(out / "cleanup.json", cleanup)
        write(out / "comparison.json", result)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    print(json.dumps(result))
    if 'exit_code' in globals():
        code = exit_code(result)
    else:
        known = [case.get('supervision', {}).get('returncode') for case in cases.values()]
        code = next((value if 0 < value < 256 else 1 for value in known if type(value) is int and value != 0), 1)
    return code if code != 0 else 128 + interrupted if interrupted else code


if __name__ == "__main__":
    raise SystemExit(main())
