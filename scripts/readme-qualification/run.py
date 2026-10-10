"""One immutable README native gate. No aggregate tests or target file writes."""
import sys
sys.dont_write_bytecode = True
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'  # Owned Python subprocesses inherit the same policy.
import hashlib
import json
from pathlib import Path
import platform
import re
import signal
import subprocess
import time

HEAD = '557ccb5ca6f201dcff6f2deb5cacc99c2b11b34f'
LOCK = '93535904b69ec9f551e120066edca66d3488d5969f6b3c3fb464474a583181c8'
README = 'ea385c1ae2db8d1dd6a1793397ddc70a6a4b4d4965a20ee24d9be6ef012861ec'
SNIPPET = '0d3f6c723ab39237342b04f262e4c4fa19c59885f9252e8578e23aecc6ad81fb'
HERE = Path(__file__).resolve().parent
PINS = {'foldkit': '0.165.0', '@foldkit/ui': '0.165.0', 'effect': '4.0.0', '@gpuix/native': '0.10.0',
        'happy-dom': '20.14.5', '@gpuix/native-darwin-arm64': '0.10.0'}


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def save(out, name, value):
    (out / f'{name}.json').write_text(json.dumps(value, indent=2))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def members(pgid):
    rows = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,pgid=,stat=,args='], text=True)
    result = []
    for row in rows.splitlines():
        values = row.strip().split(None, 4)
        if len(values) == 5 and int(values[2]) == pgid and not values[3].startswith('Z'):
            result.append({'pid': int(values[0]), 'ppid': int(values[1]), 'args': values[4]})
    return result


def signal_owned(pgid, value):
    try:
        os.killpg(pgid, value)
    except ProcessLookupError:
        pass


def command(argv, cwd, env, out, name, timeout, grace=1):
    row = {'argv': list(map(str, argv)), 'cwd': str(cwd), 'started': time.time(), 'timeoutSeconds': timeout,
           'status': 'starting', 'ownedProcesses': [], 'leaked': [], 'survivors': []}
    save(out, name, row)
    child = None
    error = None
    timed_out = False
    cleanup_error = None
    try:
        with (out / f'{name}.log').open('wb') as stream:
            child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                     stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            row['pid'] = child.pid
            seen = {child.pid: {'pid': child.pid, 'args': list(map(str, argv))}}
            deadline = time.monotonic() + timeout
            try:
                while child.poll() is None:
                    for item in members(child.pid):
                        seen[item['pid']] = item
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    time.sleep(.05)
            finally:
                row['ownedProcesses'] = list(seen.values())
    except BaseException as caught:
        error = caught
    finally:
        if child is not None:
            try:
                deadline = time.monotonic() + grace
                while not timed_out and error is None and members(child.pid) and time.monotonic() < deadline:
                    time.sleep(.05)
                row['leaked'] = members(child.pid)
                if row['leaked']:
                    signal_owned(child.pid, signal.SIGTERM)
                    deadline = time.monotonic() + 2
                    while members(child.pid) and time.monotonic() < deadline:
                        time.sleep(.05)
                    if members(child.pid):
                        signal_owned(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
                row['exit'] = child.returncode
                row['survivors'] = members(child.pid)
            except BaseException as caught:
                cleanup_error = caught
                signal_owned(child.pid, signal.SIGKILL)
        row.update(finished=time.time(), error=repr(error) if error else None,
                   cleanupError=repr(cleanup_error) if cleanup_error else None)
        row['status'] = ('spawn-failed' if child is None else 'interrupted' if error else 'timed-out' if timed_out else
                         'cleanup-failed' if cleanup_error else 'leaked-children' if row['leaked'] or row['survivors'] else
                         'command-failed' if row.get('exit') != 0 else 'passed')
        save(out, name, row)
    if error:
        raise error
    require(row['status'] == 'passed', f'{name}: {row["status"]}; original exit {row.get("exit")}; see artifacts')
    return (out / f'{name}.log').read_text(errors='replace')


def source(root):
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    clean = subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True)
    readme = (root / 'README.md').read_bytes()
    lock = (root / 'package-lock.json').read_bytes()
    found = re.search(rb'## Use it\n[\s\S]*?```ts\n([\s\S]*?)\n```', readme)
    require(found is not None, 'exact README source absent')
    snippet = found[1]
    evidence = {'head': head, 'clean': clean, 'readmeSHA256': sha(readme), 'lockSHA256': sha(lock), 'snippetSHA256': sha(snippet)}
    require(head == HEAD and not clean and sha(readme) == README and sha(lock) == LOCK and sha(snippet) == SNIPPET,
            f'immutable source drift: {evidence}')
    return evidence, snippet.decode()


def driver_source(out=None, phase='before'):
    root = HERE.parent.parent
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    clean = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=root, text=True)
    if out is not None:
        try:
            save(out, f'driver-state-{phase}', {'head': head, 'expectedGITHUB_SHA': os.environ.get('GITHUB_SHA'),
                 'clean': clean, 'python': sys.version, 'executable': sys.executable,
                 'bytecodeDisabled': sys.dont_write_bytecode, 'cachePrefix': sys.pycache_prefix,
                 'subprocessBytecodeDisabled': os.environ.get('PYTHONDONTWRITEBYTECODE')})
        except OSError:
            pass  # An evidence write failure must not replace the original guard rejection.
    require(not clean and head == os.environ.get('GITHUB_SHA'), 'runner is not the clean reviewed event head')
    files = [root / '.github/workflows/hosted-readme-qualification.yml', root / '.github/workflows/hosted-readme-contrast-qualification.yml', root / '.github/workflows/hosted-readme-corrected-qualification.yml', *HERE.glob('*.py'), *HERE.glob('*.ts'), *HERE.glob('*.swift')]
    return {'head': head, 'clean': clean, 'files': {str(path.relative_to(root)): sha(path.read_bytes()) for path in files}}


def dependency_identity(root):
    packages = {}
    for name in PINS:
        raw = (root / 'node_modules' / name / 'package.json').read_bytes()
        info = json.loads(raw)
        packages[name] = {'name': info['name'], 'version': info['version'], 'packageSHA256': sha(raw)}
    binaries = list((root / 'node_modules/@gpuix/native-darwin-arm64').glob('*.node'))
    require(len(binaries) == 1, 'missing/ambiguous native platform binary')
    native = packages['@gpuix/native-darwin-arm64']
    return {'packages': packages, 'binary': {'package': native['name'], 'version': native['version'],
            'file': binaries[0].name, 'sha256': sha(binaries[0].read_bytes())}}


def validate(out):
    rows = json.loads((out / 'observations.json').read_text())
    require([r['kind'] for r in rows] == ['startup', 'before', 'input', 'after', 'close-request', 'exit'], 'missing/extra/skipped native stages')
    startup, before, delivered, after, closing, exited = rows
    require(startup['native'] is True and startup['platform'] == 'darwin' and startup['arch'] == 'arm64' and startup['bun'] == '1.3.9', 'wrong native host/runtime')
    require(len({r['pid'] for r in rows}) == 1 and startup['pid'] > 0, 'inconsistent child identity')
    for count, row in enumerate([before, after]):
        require(row['count'] == count and f'Count: {count}' in row['text'] and f'Count: {count}' in row['painted'] and '+1' in row['painted'], 'missing real painted counter control')
        require(row['children'] > 0 and row['frames'] > 0 and row['tree'] is not None, 'unmounted/unpainted native app')
        require(row['containerConnected'] is False and row['buttonConnected'] is True, 'original runtime replacement not mounted')
        png = out / f'count-{count}.png'
        data = png.read_bytes()
        shot = row['screenshot']
        require(data[:8] == b'\x89PNG\r\n\x1a\n' and len(data) > 100 and shot['varied'] > 100, 'missing/empty painted screenshot')
        require(int.from_bytes(data[16:20], 'big') == shot['width'] > 0 and int.from_bytes(data[20:24], 'big') == shot['height'] > 0, 'PNG dimensions differ')
    require(after['frames'] > before['frames'] and 'Count: 0' not in after['text'] and 'Count: 0' not in after['painted'], 'native input did not advance the original model and paint')
    require(delivered['method'] == 'simulateClick' and delivered['id'] == before['id'] > 0 and delivered['bounds'] == before['bounds'] and delivered['window'] == before['window'], 'stale/non-native input target')
    box, window, point = delivered['bounds'], delivered['window'], delivered['point']
    require(0 <= point['x'] < window['width'] and 0 <= point['y'] < window['height'] and
            box['x'] <= point['x'] < box['x'] + box['width'] and box['y'] <= point['y'] < box['y'] + box['height'], 'input outside visible native target')
    require(closing['reason'] == 'close' and closing['beforeClose'] > 0 and closing['containerConnected'] is False and closing['buttonConnected'] is True, 'original JS close not exercised')
    require(exited['code'] == 0 and exited['closeRequests'] == 1 and exited['beforeClose'] > 0 and exited['children'] == 0 and
            exited['containerRestored'] is True and exited['buttonDisconnected'] is True and exited['disposalError'] is None and
            exited['hostDetached'] is True and exited['globalsRestored'] is True and exited['stateDetached'] is True, 'owned disposal/default clean exit not established')
    child = json.loads((out / 'native-readme.json').read_text())
    require(child['status'] == 'passed' and child['exit'] == 0 and child['pid'] == startup['pid'] and
            not child['leaked'] and not child['survivors'] and child['cleanupError'] is None, 'child exit/owned cleanup failed')


def run(root, out, case='original'):
    out.mkdir(parents=True, exist_ok=True)  # Before every preflight or launch failure.
    result = {'status': 'failed', 'phase': 'preflight', 'head': HEAD, 'started': time.time(),
              'host': {'system': platform.system(), 'machine': platform.machine()}, 'error': None, 'case': case}
    driver = None
    dependencies = None
    probe = None
    probe_identity = None
    save(out, 'result', result)
    try:
        require(case in ('original', 'control', 'corrected'), 'unknown qualification case')
        require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'requires real macOS Apple silicon host')
        require('FOLDKIT_NATIVE_NO_WINDOW' not in os.environ, 'headless mode forbidden')
        require('NAPI_RS_NATIVE_LIBRARY_PATH' not in os.environ, 'alternate native binding forbidden')
        driver = driver_source(out, 'before')
        save(out, 'driver-before', driver)
        before, snippet = source(root)
        save(out, 'source-before', before)
        (out / 'original-snippet.ts').write_text(snippet)
        env = {**os.environ, 'QUALIFICATION_TARGET': str(root), 'QUALIFICATION_EVIDENCE': str(out), 'QUALIFICATION_CASE': case}
        for key in ('BUN_PRELOAD', 'NODE_OPTIONS', 'FOLDKIT_NATIVE_AUTOMATION', 'FOLDKIT_NATIVE_RENDERER'):
            env.pop(key, None)
        result['node'] = command(['node', '--version'], root, env, out, 'node-version', 10).strip()
        result['bun'] = command(['bun', '--version'], root, env, out, 'bun-version', 10).strip()
        require(result['node'] == 'v22.22.3' and result['bun'] == '1.3.9', 'runtime pins differ')
        result['phase'] = 'dependencies'
        command(['npm', 'ci'], root, env, out, 'npm-ci', 180)
        dependencies = dependency_identity(root)
        save(out, 'dependencies-before', dependencies)
        pins = {name: info['version'] for name, info in dependencies['packages'].items()}
        require(pins == PINS and all(info['name'] == name for name, info in dependencies['packages'].items()), 'dependency pins/identities differ')
        result['pins'] = pins
        save(out, 'native-binary', dependencies['binary'])
        if case in ('control', 'corrected'):
            probe = out / 'macos-frame'
            command(['swiftc', str(HERE / 'macos-frame.swift'), '-o', str(probe)], root, env, out, 'compile-frame-probe', 60)
            probe_identity = sha(probe.read_bytes())
            save(out, 'frame-probe', {'sourceSHA256': sha((HERE / 'macos-frame.swift').read_bytes()), 'binarySHA256': probe_identity})
            env['QUALIFICATION_FRAME_PROBE'] = str(probe)
            if case == 'control':
                from contrast_run import control_source
                snippet = control_source(snippet)
                (out / 'control-snippet.ts').write_text(snippet)
        # Eval runs from the documented repository root, using its exact tsconfig
        # and imports. Original and corrected cases are verbatim; the separately
        # labeled control changes the css option and saves its own source/hash.
        evaluation = f'import {{ begin as qualificationBegin }} from {json.dumps(str(HERE / "witness.ts"))};\nconst qualificationObserve = qualificationBegin();\n' + snippet + '\nawait qualificationObserve(app);\n'
        (out / 'executed-source.ts').write_text(evaluation)
        result['executedSHA256'] = sha(evaluation.encode())
        result['phase'] = 'native-readme'
        command(['bun', '--eval', evaluation], root, env, out, 'native-readme', 45)
        result['phase'] = 'acceptance'
        validate(out)
        if case in ('control', 'corrected'):
            from contrast_run import validate_control
            validate_control(out, case)
        result['status'] = 'passed'
    except BaseException as error:
        result['error'] = repr(error)
        raise
    finally:
        try:
            after, _ = source(root)
            save(out, 'source-after', after)
            after_driver = driver_source(out, 'after')
            save(out, 'driver-after', after_driver)
            require(after_driver == driver, 'qualification driver changed during execution')
        except BaseException as error:
            result['status'] = 'failed'
            result['sourceError'] = repr(error)
        # Check installed inputs even when source revalidation or execution failed.
        if dependencies is not None:
            try:
                after_dependencies = dependency_identity(root)
                save(out, 'dependencies-after', after_dependencies)
                save(out, 'native-binary-after', after_dependencies['binary'])
                require(after_dependencies == dependencies, 'dependency/native binary identity changed during execution')
            except BaseException as error:
                result['status'] = 'failed'
                result['dependencyError'] = repr(error)
                save(out, 'dependencies-after-failure', {'error': repr(error)})
        if probe_identity is not None:
            try:
                after_probe = sha(probe.read_bytes())
                save(out, 'frame-probe-after', {'binarySHA256': after_probe})
                require(after_probe == probe_identity, 'window probe binary changed')
            except BaseException as error:
                result['status'] = 'failed'
                result['probeError'] = repr(error)
        result['finished'] = time.time()
        save(out, 'result', result)
        manifest = {str(path.relative_to(out)): sha(path.read_bytes()) for path in out.rglob('*') if path.is_file() and path.name != 'manifest.json'}
        save(out, 'manifest', manifest)
    require(result['status'] == 'passed', 'final source/dependency identity failed')


if __name__ == '__main__':
    def interrupted(signum, _frame):
        raise InterruptedError(f'supervisor signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    run(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
