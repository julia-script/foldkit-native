"""Separate original/control results. A control pass NEVER accepts a failed original."""
import json
import math
from pathlib import Path
import signal
import sys
import run as original

CSS = ' body, .app { background-color: #ffffff; color: #000000; }'
MOUNT = "mountGpuix({ title: 'My app', width: 800, height: 600, css })"


def control_source(snippet):
    original.require(original.sha(snippet.encode()) == original.SNIPPET and snippet.count(MOUNT) == 1,
                     'control must derive from the exact immutable original snippet')
    return snippet.replace(MOUNT, MOUNT.replace('css })', 'css: css + ' + json.dumps(CSS) + ' })'))


def validate_control(out):
    rows = json.loads((out / 'observations.json').read_text())
    initial = json.loads((out / 'initial-styles.json').read_text())
    original.require(initial['case'] == 'control' and initial['pid'] == rows[0]['pid'], 'missing/wrong control styles')
    shots = []
    for count, row in enumerate([rows[1], rows[3]]):
        before = json.loads((out / f'count-{count}-contrast-before.json').read_text())
        after = json.loads((out / f'count-{count}-contrast-after.json').read_text())
        shot = json.loads((out / f'count-{count}-contrast-pixels.json').read_text())
        original.require(before['pid'] == after['pid'] == shot['pid'] == rows[0]['pid'] > 0 and
                         before['probe']['pid'] == after['probe']['pid'] == rows[0]['pid'], 'wrong contrast child')
        original.require(before['probe'] == after['probe'] and before['probe']['windowId'] > 0 and
                         before['window'] == after['window'] == row['window'] and
                         before['regions'] == after['regions'] and before['styles'] == after['styles'] and
                         before['count'] == after['count'] == shot['count'] == count and
                         after['frames'] >= before['frames'] > 0 and 0 <= after['at'] - before['at'] <= 5000,
                         'stale scene/style/geometry')
        if count == 0:
            original.require(initial['styles'] == before['styles'], 'initial styles differ from captured original control state')
            first_probe, first_regions = before['probe'], before['regions']
        else:
            original.require(before['probe'] == first_probe and
                             [r['bounds'] for r in before['regions']] == [r['bounds'] for r in first_regions],
                             'owned window/regions moved between counter captures')
        styles = after['styles']
        original.require([v['selector'] for v in styles] == ['html', 'body', '.app', 'p', 'button'] and
                         all(v['color'] == '#000000' for v in styles) and
                         all(v['background'] == '#ffffff' for v in styles if v['selector'] in ('body', '.app')) and
                         styles[3]['text'] == f'Count: {count}' and styles[4]['text'] == '+1', 'wrong contrast styles/text')
        original.require(shot['scale'] == after['probe']['scale'] > 0 and
                         shot['width'] == row['screenshot']['width'] == after['window']['width'] * shot['scale'] and
                         shot['height'] == row['screenshot']['height'] == after['window']['height'] * shot['scale'], 'wrong logical/device scale')
        original.require(shot['pngSHA256'] == original.sha((out / f'count-{count}.png').read_bytes()), 'contrast PNG receipt differs from actual image')
        original.require([r['label'] for r in shot['samples']] == ['app', f'Count: {count}', '+1'], 'missing contrast region')
        for sample, region in zip(shot['samples'], after['regions']):
            box, scale = region['bounds'], shot['scale']
            original.require(all(math.isfinite(v) for v in box.values()) and box['width'] > 0 and box['height'] > 0 and
                             box['x'] >= 0 and box['y'] >= 0 and box['x'] + box['width'] <= after['window']['width'] and
                             box['y'] + box['height'] <= after['window']['height'], 'region outside actual native content')
            device = {'left': math.ceil(box['x'] * scale), 'top': math.ceil(box['y'] * scale),
                      'right': math.floor((box['x'] + box['width']) * scale), 'bottom': math.floor((box['y'] + box['height']) * scale)}
            area = (device['right'] - device['left']) * (device['bottom'] - device['top'])
            original.require(sample['id'] == region['id'] > 0 and sample['bounds'] == region['bounds'] and
                             sample['device'] == device and sample['area'] == area > 0 and
                             sample['white'] > 100 and sample['white'] / sample['area'] > .5 and
                             original.re.fullmatch('[0-9a-f]{64}', sample['inkSHA256']) and
                             0 <= sample['dark'] <= area and sample['dark'] + sample['white'] <= area, 'missing measured white region')
            if sample['label'] != 'app':
                original.require(sample['dark'] >= 10 and sample['dark'] / sample['area'] < .5, 'missing measured foreground')
        shots.append(shot)
    original.require(shots[0]['samples'][1]['inkSHA256'] != shots[1]['samples'][1]['inkSHA256'],
                     'Count1 pixels did not change after genuine input')


def summarize(out):
    cases = {case: json.loads((out / case / 'result.json').read_text()) for case in ('original', 'control')}
    for case in cases:
        original.require(cases[case]['case'] == case, 'mislabeled case result')
        original.require(cases[case]['status'] in ('passed', 'failed') and cases[case]['head'] == original.HEAD, 'unknown/wrong target case result')
    pids = []
    for case in cases:
        command = out / case / 'native-readme.json'
        original.require(command.exists() or cases[case]['status'] == 'failed', 'passed case missing native child')
        if command.exists():
            child = json.loads(command.read_text())
            original.require(not child['survivors'] and not child['leaked'] and child['cleanupError'] is None,
                             'owned child failure cannot be accepted')
            if cases[case]['status'] == 'passed':
                original.require(child['status'] == 'passed' and child['exit'] == 0 and child['pid'] > 0,
                                 'passed result conflicts with original native exit')
            if 'pid' in child:
                pids.append(child['pid'])
    original.require(len(pids) == len(set(pids)), 'cases reused a native PID')
    return {'status': 'passed' if all(c['status'] == 'passed' for c in cases.values()) else 'failed',
            'originalAcceptance': cases['original']['status'], 'controlDiagnostic': cases['control']['status'],
            'cases': cases, 'pids': pids}


def run(root, out):
    out.mkdir(parents=True, exist_ok=True)
    original.save(out, 'result', {'status': 'failed', 'phase': 'before-cases'})
    try:
        for case in ('original', 'control'):
            destination = out / case
            try:
                original.run(root, destination, case)
            except InterruptedError:
                raise
            except Exception:
                # Preserve the case's own error/exit and artifacts. A failed original
                # may precede this independent control, but never becomes accepted.
                case_result = json.loads((destination / 'result.json').read_text())
                original.require(not case_result.get('sourceError') and not case_result.get('dependencyError'), 'stop after source/dependency identity failure')
                child_path = destination / 'native-readme.json'
                if child_path.exists():
                    child = json.loads(child_path.read_text())
                    original.require(not child['survivors'] and not child['leaked'] and child['cleanupError'] is None,
                                     'stop before another case after owned cleanup failure')
        result = summarize(out)
        original.save(out, 'result', result)
        original.require(result['status'] == 'passed', 'original/control diagnostics contain a failed case; no original acceptance')
    finally:
        original.save(out, 'manifest', {str(p.relative_to(out)): original.sha(p.read_bytes()) for p in out.rglob('*') if p.is_file() and p != out / 'manifest.json'})


if __name__ == '__main__':
    def interrupted(signum, _frame):
        raise InterruptedError(f'supervisor signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    run(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
