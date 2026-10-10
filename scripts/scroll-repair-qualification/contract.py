"""Four original outcomes; evidence completeness never turns failure into pass."""
import hashlib
import json
import math
import re

BASE = '7b5aaf49eb9ee49b5c6a596a8e6db8d1207f25fa'
CANDIDATE = '2c8347cb327a11d4f6bee4aaeb48a82f4774f764'
SPECS = {
    'big_list': {'title': 'a wheel scrolls the list natively and FoldKit renders the rows it reaches', 'file': './examples/big-list/native.test.ts', 'window': 'big-list', 'assertions': 8, 'screenshots': ['big-list-scrolled.png', 'big-list-end.png']},
    'pixel32': {'title': 'a drag across a 32 × 32 board through GPUI: every cell it crosses, and how many moves a second', 'file': './examples/pixel-art/native.test.ts', 'window': 'pixel-art', 'assertions': 33, 'screenshots': ['pixel-art-big-stroke.png']},
}
CASES = {name: {'head': head, 'test': test} for test in SPECS for name, head in [('baseline_' + test, BASE), ('candidate_' + test, CANDIDATE)]}
DELTA = {'packages/foldkit-gpuix/src/host.ts', 'packages/foldkit-gpuix/test/scroll-guard.test.ts', 'scripts/scroll-safe-checks.py', 'scripts/scroll-safe-checks-controls.py'}
DRIVER = {'.github/workflows/scroll-repair-qualification.yml', *{'scripts/scroll-repair-qualification/' + name for name in ('contract.py', 'run.py', 'supervise.py', 'controls.py', 'safe.py')}}
METHODS = ['nativeSimulateClick', 'nativeSimulateMouseDown', 'nativeSimulateMouseUp', 'nativeSimulateScrollWheel', 'nativeSimulateKeyDown', 'nativeSimulateKeyUp', 'dispatchMouseMove', 'flush', 'dispatchNativeEvents', 'getWindowSize', 'getElementBounds', 'getScrollOffset', 'getPaintedText', 'getAutomationTree', 'captureScreenshot']
BLOCK_ENV = {'NAPI_RS_NATIVE_LIBRARY_PATH', 'NAPI_RS_FORCE_WASI', 'NODE_OPTIONS', 'BUN_OPTIONS', 'BUN_PRELOAD', 'THROTTLE', 'GPUIX_CPU_THROTTLE_APPLIED'}


def command_for(name, bun):
    spec = SPECS[CASES[name]['test']]
    return [bun, 'test', '--preload', './test/support/dom.ts', spec['file'], '--test-name-pattern', '^Metal, offscreen.*' + re.escape(spec['title']) + '$']


def capability_command(bun):
    code = "import { TestRenderer, hasNativeTestRenderer } from '@gpuix/native/testing'; const names=" + json.dumps(METHODS) + "; console.log('scroll-qualification-capability:'+JSON.stringify({nativeAvailable:hasNativeTestRenderer,platform:process.platform,arch:process.arch,bun:Bun.version,modulePath:Bun.resolveSync('@gpuix/native/testing',process.cwd()),bindingPath:Bun.resolveSync('@gpuix/native-darwin-arm64',process.cwd()),methods:Object.fromEntries(names.map(n=>[n,typeof TestRenderer.prototype[n]==='function']))}));"
    return [bun, "--eval", code]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def obj(value):
    return value if isinstance(value, dict) else {}


def finite(value, positive=False):
    return type(value) in (float, int) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def sha(value):
    return bool(re.fullmatch('[0-9a-f]{64}', str(value)))


def cleaned(state):
    state = obj(state)
    return (state.get('started') is True and state.get('reaped') is True and state.get('groupClean') is True
            and state.get('deadlineExceeded') is False and not state.get('failure') and not state.get('inspectionFailure')
            and state.get('remainingGroupMembers') == [] and type(state.get('returncode')) is int
            and finite(state.get('elapsedSeconds'), True) and state.get('naturalExit') is True and state.get('signals') == [])


def native_capability(cap, dependency):
    cap, dependency = obj(cap), obj(dependency)
    return (cap.get('nativeAvailable') is True and cap.get('platform') == 'darwin' and cap.get('arch') == 'arm64'
            and cap.get('bun') == '1.3.9' and cap.get('modulePath') == dependency.get('testingPath')
            and cap.get('bindingPath') == dependency.get('bindingPath') and cap.get('methods') == {name: True for name in METHODS})


def observations(raw, name):
    raw = re.sub(r'\x1b\[[0-9;]*m', '', raw if isinstance(raw, str) else '')
    spec = SPECS[CASES[name]['test']]
    windows, drag = [], []
    for index, line in enumerate(raw.splitlines()):
        for prefix, output in [('native test window:', windows), ('pixel-art Metal drag:', drag)]:
            if prefix in line:
                try:
                    output.append({'line': index + 1, 'value': json.loads(line.split(prefix, 1)[1].strip())})
                except ValueError:
                    output.append({'line': index + 1, 'malformed': True})
    verdicts = re.findall(r'^\((pass|fail|skip)\) (.+)$', raw, re.MULTILINE)
    title_pattern = r'^Metal, offscreen > ' + re.escape(spec['title']) + r' \[(\d+(?:\.\d+)?)(ms|s)\]$'
    selected = [(status, title) for status, title in verdicts if re.fullmatch(title_pattern, title)]
    duration = None
    if len(selected) == 1:
        match = re.fullmatch(title_pattern, selected[0][1])
        duration = float(match[1]) * (1000 if match[2] == 's' else 1)
    counts = {kind: [int(n) for n in re.findall(r'^\s*(\d+) ' + re.escape(kind) + r'\s*$', raw, re.MULTILINE)] for kind in ('pass', 'fail', 'skip', 'expect() calls')}
    runs = re.findall(r'Ran (\d+) tests? across (\d+) files?\.', raw)
    return {'selected': selected, 'otherExecuted': [(s, t) for s, t in verdicts if (s, t) not in selected],
            'durationMs': duration, 'windows': windows, 'drag': drag, 'counts': counts, 'runs': runs,
            'original30000msTimeout': bool(re.search(r'(?:timed out|timeout).*30000', raw, re.I))}


def validate_case(name, case, expected_source, dependency, bun):
    case, dependency = obj(case), obj(dependency)
    spec, problems = SPECS[CASES[name]['test']], []
    state = obj(case.get('supervision'))
    result = observations(case.get('raw'), name)
    selected = result['selected']
    outcome = selected[0][0] if len(selected) == 1 else 'unproved'
    if case.get('name') != name or case.get('sourceHead') != CASES[name]['head'] or case.get('executionKind') != 'hosted-native':
        problems.append('source or genuine execution provenance mismatch')
    if obj(expected_source).get('head') != CASES[name]['head']:
        problems.append('expected immutable source identity missing')
    if not sha(dependency.get('bindingSha256')) or not sha(dependency.get('installedDigest')):
        problems.append('canonical dependency/native identity missing')
    if case.get('command') != command_for(name, bun) or state.get('command') != case.get('command'):
        problems.append('original focused command changed')
    if obj(case.get('originalEnvironment')).get('FOLDKIT_NATIVE_RENDERER') != 'gpuix' or state.get('envelopeSeconds') != 60:
        problems.append('original renderer or supervisor envelope mismatch')
    if not cleaned(state):
        problems.append('original did not exit naturally with verified owned cleanup')
    cap_state = obj(case.get('capabilitySupervision'))
    if not cleaned(cap_state) or cap_state.get('returncode') != 0 or cap_state.get('envelopeSeconds') != 15 or cap_state.get('command') != capability_command(bun) or not native_capability(case.get('capability'), dependency):
        problems.append('native capability unproved')
    prepare = obj(case.get('prepareSupervision'))
    if not cleaned(prepare) or prepare.get('returncode') != 0:
        problems.append('owned immutable checkout preparation unproved')
    for phase in ('before', 'after'):
        if obj(case.get(phase)).get('source') != expected_source or obj(case.get(phase)).get('dependencies') != dependency:
            problems.append(phase + ' source/dependency/binding identity mismatch')
    if len(selected) != 1 or outcome not in ('pass', 'fail') or result['otherExecuted'] or not finite(result['durationMs'], True):
        problems.append('exactly one original non-skipped result and duration required')
    expected_counts = {'pass': [1 if outcome == 'pass' else 0], 'fail': [1 if outcome == 'fail' else 0], 'skip': []}
    # Bun omits the zero-skip summary. Explicit 0 skip is also accepted.
    counts = result['counts']
    if counts['pass'] != expected_counts['pass'] or counts['fail'] != expected_counts['fail'] or counts['skip'] not in ([], [0]) or result['runs'] != [('1', '1')]:
        problems.append('original summary must contain one test/file with no skips')
    assertions = counts['expect() calls']
    if len(assertions) != 1 or (outcome == 'pass' and assertions != [spec['assertions']]) or (outcome == 'fail' and not 1 <= assertions[0] <= spec['assertions']):
        problems.append('original assertion count missing or altered')
    original_exit = state.get('returncode')
    if outcome == 'pass' and original_exit != 0 or outcome == 'fail' and (type(original_exit) is not int or original_exit <= 0):
        problems.append('original verdict disagrees with raw exit')
    windows = result['windows']
    if len(windows) != 1 or not isinstance(windows[0].get('value'), dict):
        problems.append('original real native window missing')
    else:
        window = windows[0]['value']
        if window.get('name') != spec['window'] or any(any(not finite(obj(window.get(size)).get(axis), True) for axis in ('width', 'height')) for size in ('requested', 'actual')):
            problems.append('original window identity or geometry invalid')
    screenshots = obj(case.get('screenshots'))
    if set(screenshots) != set(spec['screenshots']):
        problems.append('separate original screenshot receipts missing')
    for file in spec['screenshots']:
        png = obj(screenshots.get(file))
        if png.get('available') is True:
            if png.get('validPng') is not True or not finite(png.get('width'), True) or not finite(png.get('height'), True) or not sha(png.get('sha256')):
                problems.append('corrupt available original screenshot: ' + file)
        elif png.get('available') is not False or outcome == 'pass':
            problems.append('successful original screenshot missing: ' + file)
    if outcome == 'pass' and CASES[name]['test'] == 'pixel32':
        drag = result['drag']
        if len(drag) != 1 or obj(drag[0].get('value')).get('moves') != 62 or obj(drag[0].get('value')).get('cells') != 1024 or not finite(obj(drag[0].get('value')).get('ms'), True):
            problems.append('original successful 62-move/1024-cell timing missing')
    return {'completeEvidence': not problems, 'problems': problems, 'originalResult': outcome,
            'originalExit': original_exit, 'observations': result, 'screenshots': screenshots,
            'note': 'Original assertions remain authoritative. Failure and any post-timeout screenshot are not completed paint proof.'}


def validate_all(receipt, cases, expected, dependency, bun, cleanup):
    receipt, cases, expected, cleanup = map(obj, (receipt, cases, expected, cleanup))
    reports = {name: validate_case(name, cases.get(name), expected.get(name), dependency, bun) for name in CASES}
    problems = []
    if (receipt.get('executionKind') != 'hosted-native' or receipt.get('platform') != 'Darwin' or receipt.get('machine') != 'arm64'
            or receipt.get('githubRepository') != 'julia-script/foldkit-native' or not str(receipt.get('githubRunId', '')).isdigit()
            or receipt.get('githubHead') != receipt.get('driverHead') or not re.fullmatch('[0-9a-f]{40}', str(receipt.get('driverHead')))):
        problems.append('actual hosted provenance unavailable or synthetic')
    install = obj(receipt.get('installSupervision'))
    if receipt.get('bun') != '1.3.9' or receipt.get('node') != 'v22.22.3' or not cleaned(install) or install.get('returncode') != 0 or install.get('command') != ['npm', 'ci', '--ignore-scripts']:
        problems.append('pinned runtime/owned installation unproved')
    if receipt.get('sourceDelta') != {'base': BASE, 'candidate': CANDIDATE, 'files': sorted(DELTA)}:
        problems.append('intentional source delta unproved')
    if set(cases) != set(CASES) or any(not r['completeEvidence'] for r in reports.values()):
        problems.append('four separate original outcomes incomplete')
    groups, sources, capability_groups, prior_end = [], [], [], None
    for name in CASES:
        case = obj(cases.get(name)); state = obj(case.get('supervision'))
        groups.append(state.get('pgid')); sources.append(case.get('sourcePath')); capability_groups.append(obj(case.get('capabilitySupervision')).get('pgid'))
        start, end = case.get('originalStartedEpoch'), case.get('originalEndedEpoch')
        if not finite(start, True) or not finite(end, True) or start > end or prior_end is not None and start < prior_end:
            problems.append('serial fresh original sessions unproved: ' + name)
        prior_end = end if finite(end, True) else None
        if cleanup.get(name) is not True:
            problems.append('owned checkout cleanup incomplete: ' + name)
    if any(type(pid) is not int or pid <= 0 for pid in groups) or len(set(groups)) != 4 or any(not isinstance(path, str) or not path for path in sources) or len(set(sources)) != 4:
        problems.append('four fresh isolated original groups/sources unproved')
    if any(type(pid) is not int or pid <= 0 for pid in capability_groups) or len(set(groups + capability_groups)) != 8:
        problems.append('fresh per-case capability sessions unproved')
    if (receipt.get('driverSourceBefore') != receipt.get('driverSourceAfter') or obj(receipt.get('driverSourceBefore')).get('head') != receipt.get('driverHead')
            or receipt.get('dependencyIdentity') != dependency or receipt.get('dependencyAfter') != dependency):
        problems.append('driver/shared canonical identity drift')
    if set(cleanup) != set(CASES) or any(value is not True for value in cleanup.values()):
        problems.append('complete owned checkout/owner cleanup unproved')
    complete = not problems
    return {'completeComparisonEvidence': complete, 'problems': problems, 'cases': reports,
            'candidateOriginalTestsPassed': complete and all(reports[n]['originalResult'] == 'pass' for n in CASES if n.startswith('candidate_')),
            'baselineOriginalTestsPassed': complete and all(reports[n]['originalResult'] == 'pass' for n in CASES if n.startswith('baseline_')),
            'allOriginalTestsPassed': complete and all(r['originalResult'] == 'pass' for r in reports.values()),
            'note': 'One focused pair per test proves neither repeatability, speedup, Pixel timeout cause nor general delivery.'}


def exit_code(result):
    for name in CASES:
        raw = obj(obj(result).get('cases')).get(name)
        value = obj(raw).get('originalExit')
        if type(value) is int and value != 0:
            return value if 0 < value < 256 else 1
    return 0 if obj(result).get('completeComparisonEvidence') is True else 1
