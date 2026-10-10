"""Result/source fault controls only. No GPUI or real native evaluation."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import contrast_run as runner
import run as original
import run_test as fixtures


class Contrast(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def child(self, case, status='passed', pid=123):
        path = self.out / case
        path.mkdir(exist_ok=True)
        original.save(path, 'result', {'head': original.HEAD, 'case': case, 'status': status, 'error': 'original guard' if status == 'failed' else None})
        original.save(path, 'native-readme', {'pid': pid, 'exit': 1 if status == 'failed' else 0, 'status': 'command-failed' if status == 'failed' else 'passed', 'leaked': [], 'survivors': [], 'cleanupError': None})
        source = {'head': original.HEAD, 'clean': '', 'readmeSHA256': original.README, 'lockSHA256': original.LOCK, 'snippetSHA256': original.SNIPPET}
        driver = {'head': 'd'*40, 'clean': '', 'files': {'witness.ts': 'a'*64}}
        dependencies = {'packages': {name: {'name': name, 'version': version, 'packageSHA256': 'a'*64} for name, version in original.PINS.items()},
                        'binary': {'package': '@gpuix/native-darwin-arm64', 'version': '0.10.0', 'file': 'mock.node', 'sha256': 'a'*64}}
        for phase in ['before', 'after']:
            original.save(path, 'source-'+phase, source)
            original.save(path, 'driver-'+phase, driver)
            original.save(path, 'dependencies-'+phase, dependencies)
        original.save(path, 'native-binary', dependencies['binary'])
        original.save(path, 'native-binary-after', dependencies['binary'])

    def test_cross_case_identical_inputs_pass_and_different_frozen_inputs_reject(self):
        self.child('original'); self.child('control', pid=124)
        self.assertEqual(runner.summarize(self.out)['status'], 'passed')
        for kind in ['binary', 'package', 'source', 'driver']:
            self.child('control', pid=124)
            for phase in ['before', 'after']:
                name = 'dependencies' if kind in ['binary', 'package'] else kind
                data = json.loads((self.out / f'control/{name}-{phase}.json').read_text())
                if kind == 'binary': data['binary']['sha256'] = 'b'*64
                elif kind == 'package': data['packages']['effect']['packageSHA256'] = 'b'*64
                elif kind == 'source': data['readmeSHA256'] = 'b'*64
                else: data['files']['witness.ts'] = 'b'*64
                original.save(self.out / 'control', name+'-'+phase, data)
                if kind == 'binary': original.save(self.out / 'control', 'native-binary' if phase == 'before' else 'native-binary-after', data['binary'])
            with self.subTest(kind=kind), self.assertRaises((RuntimeError, KeyError)): runner.summarize(self.out)

    def test_missing_or_per_case_identity_drift_rejects_without_rewriting_case_error_exit(self):
        for name in ['source-before', 'source-after', 'driver-before', 'driver-after', 'dependencies-before', 'dependencies-after', 'native-binary', 'native-binary-after']:
            self.child('original', 'failed'); self.child('control', pid=124)
            frozen = (self.out / 'original/result.json').read_bytes(), (self.out / 'original/native-readme.json').read_bytes()
            (self.out / 'control' / (name+'.json')).unlink()
            with self.subTest(name=name), self.assertRaises((RuntimeError, FileNotFoundError)): runner.summarize(self.out)
            self.assertEqual(frozen, ((self.out / 'original/result.json').read_bytes(), (self.out / 'original/native-readme.json').read_bytes()))
        self.child('control', pid=124)
        packet = json.loads((self.out / 'control/dependencies-after.json').read_text()); packet['binary']['sha256'] = 'b'*64
        original.save(self.out / 'control', 'dependencies-after', packet)
        with self.assertRaises(RuntimeError): runner.summarize(self.out)

    def test_aggregate_identity_failure_preserves_original_case_error_exit_and_manifests(self):
        frozen = {}
        def run(_root, destination, case):
            self.child(case, 'failed' if case == 'original' else 'passed', 123 if case == 'original' else 124)
            if case == 'original':
                frozen['result'] = (destination / 'result.json').read_bytes()
                frozen['child'] = (destination / 'native-readme.json').read_bytes()
                raise RuntimeError('original native guard failed')
            for phase in ['before', 'after']:
                packet = json.loads((destination / f'dependencies-{phase}.json').read_text())
                packet['binary']['sha256'] = 'b'*64
                original.save(destination, 'dependencies-'+phase, packet)
                original.save(destination, 'native-binary' if phase == 'before' else 'native-binary-after', packet['binary'])
        with patch.object(original, 'run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'identities differ'): runner.run(self.out, self.out)
        aggregate = json.loads((self.out / 'result.json').read_text())
        self.assertEqual(aggregate['status'], 'failed')
        self.assertIn('identities differ', aggregate['error'])
        self.assertEqual(aggregate['cases']['original']['error'], 'original guard')
        self.assertEqual(aggregate['cases']['control']['status'], 'passed')
        self.assertEqual(frozen['result'], (self.out / 'original/result.json').read_bytes())
        self.assertEqual(frozen['child'], (self.out / 'original/native-readme.json').read_bytes())
        manifest = json.loads((self.out / 'manifest.json').read_text())
        self.assertIn('original/dependencies-before.json', manifest)
        self.assertIn('control/native-binary-after.json', manifest)

    def test_control_pass_does_not_accept_failed_original(self):
        self.child('original', 'failed', 123)
        self.child('control', 'passed', 124)
        result = runner.summarize(self.out)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['originalAcceptance'], 'failed')
        self.assertEqual(result['controlDiagnostic'], 'passed')
        self.assertEqual(result['pids'], [123, 124])

    def test_control_source_changes_only_documented_css_option(self):
        snippet = '// synthetic source\nconst app = ' + runner.MOUNT
        with patch.object(original, 'SNIPPET', original.sha(snippet.encode())):
            controlled = runner.control_source(snippet)
            self.assertEqual(controlled.replace('css: css + ' + json.dumps(runner.CSS), 'css'), snippet)
            with self.assertRaisesRegex(RuntimeError, 'immutable'): runner.control_source(snippet + '// drift')

    def test_wrong_result_missing_case_reused_pid_and_owned_failure_reject(self):
        self.child('original'); self.child('control', pid=124)
        original.save(self.out / 'control', 'result', {'case': 'original', 'status': 'passed'})
        with self.assertRaisesRegex(RuntimeError, 'mislabeled'): runner.summarize(self.out)
        self.child('control')
        with self.assertRaisesRegex(RuntimeError, 'PID'): runner.summarize(self.out)
        for field, value in [('leaked', [125]), ('survivors', [125]), ('cleanupError', 'failure')]:
            self.child('control', pid=124)
            child = json.loads((self.out / 'control/native-readme.json').read_text()); child[field] = value
            original.save(self.out / 'control', 'native-readme', child)
            with self.assertRaisesRegex(RuntimeError, 'owned'): runner.summarize(self.out)
        for field, value in [('exit', 17), ('status', 'command-failed'), ('pid', 0)]:
            self.child('control', pid=124)
            child = json.loads((self.out / 'control/native-readme.json').read_text()); child[field] = value
            original.save(self.out / 'control', 'native-readme', child)
            with self.assertRaisesRegex(RuntimeError, 'native exit'): runner.summarize(self.out)
        self.child('control', pid=124); (self.out / 'control/native-readme.json').unlink()
        with self.assertRaisesRegex(RuntimeError, 'missing native'): runner.summarize(self.out)
        (self.out / 'control/result.json').unlink()
        with self.assertRaises(FileNotFoundError): runner.summarize(self.out)

    def test_suite_retains_original_error_exit_and_independent_control_artifacts(self):
        def run(_root, destination, case):
            self.child(case, 'failed' if case == 'original' else 'passed', 123 if case == 'original' else 124)
            (destination / 'count-0.png').write_bytes((case + ' synthetic artifact').encode())
            if case == 'original': raise RuntimeError('unchanged original failure')
        with patch.object(original, 'run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'no original acceptance'): runner.run(self.out, self.out)
        result = json.loads((self.out / 'result.json').read_text())
        self.assertEqual(result['originalAcceptance'], 'failed')
        self.assertEqual(result['cases']['original']['error'], 'original guard')
        self.assertEqual(json.loads((self.out / 'original/native-readme.json').read_text())['exit'], 1)
        self.assertNotEqual((self.out / 'original/count-0.png').read_bytes(), (self.out / 'control/count-0.png').read_bytes())
        manifest = json.loads((self.out / 'manifest.json').read_text())
        self.assertIn('original/count-0.png', manifest); self.assertIn('control/count-0.png', manifest)

    def test_interruption_and_identity_or_cleanup_failure_stop_before_another_case(self):
        for kind in ['interrupt', 'identity', 'owned']:
            calls = []
            def run(_root, destination, case):
                calls.append(case); self.child(case, 'failed')
                if kind == 'identity': original.save(destination, 'result', {'case': case, 'status': 'failed', 'sourceError': 'drift'})
                if kind == 'owned': original.save(destination, 'native-readme', {'leaked': [999], 'survivors': [], 'cleanupError': None})
                if kind == 'interrupt': raise InterruptedError('original interrupt')
                raise RuntimeError('original failure')
            with patch.object(original, 'run', side_effect=run):
                with self.assertRaises((RuntimeError, InterruptedError)): runner.run(self.out, self.out)
            self.assertEqual(calls, ['original'])

    def test_probe_drift_and_disappearance_preserve_original_native_error(self):
        # Real runner finally/hash/manifest path, mocked commands and validation.
        # The temporary helper bytes are never an executable/native application.
        for disappear in [False, True]:
            out = self.out / ('missing' if disappear else 'drift')
            pins = {'packages': {n: {'name': n, 'version': v} for n, v in original.PINS.items()}, 'binary': {'sha256': 'mock'}}
            def command(argv, _cwd, _env, destination, name, timeout):
                if name == 'node-version': return 'v22.22.3'
                if name == 'bun-version': return '1.3.9'
                if name == 'compile-frame-probe': Path(argv[-1]).write_bytes(b'mock probe, never executed')
                if name == 'native-readme':
                    self.assertEqual(timeout, 45)
                    probe = destination / 'macos-frame'
                    if disappear: probe.unlink()
                    else: probe.write_bytes(b'changed')
                    original.save(destination, 'native-readme', {'status': 'command-failed', 'exit': 17})
                    raise RuntimeError('original native exit17')
                return ''
            with patch.object(original.platform, 'system', return_value='Darwin'), patch.object(original.platform, 'machine', return_value='arm64'), patch.object(original, 'source', return_value=({}, '// mocked snippet')), patch.object(original, 'driver_source', return_value={}), patch.object(original, 'dependency_identity', return_value=pins), patch.object(original, 'command', side_effect=command), patch.object(runner, 'control_source', return_value='// separate mocked control'):
                with self.assertRaisesRegex(RuntimeError, 'original native exit17'): original.run(self.out, out, 'control')
            result = json.loads((out / 'result.json').read_text())
            self.assertEqual(result['status'], 'failed')
            self.assertIn('probeError', result)
            self.assertIn('original native exit17', result['error'])
            self.assertEqual(json.loads((out / 'native-readme.json').read_text())['exit'], 17)
            self.assertTrue((out / 'manifest.json').exists())


class ControlReceipt(unittest.TestCase):
    # Inherit the original meaningful acceptance negatives unchanged, and add
    # independently labeled contrast receipts on their synthetic PNG controls.
    def setUp(self):
        fixtures.Acceptance.setUp(self)
        self.contexts = []
        self.shots = []
        for count, row in enumerate([self.rows[1], self.rows[3]]):
            row['window'] = {'width': 800, 'height': 600}
            styles = [{'selector': s, 'id': i+1, 'color': '#000000', 'background': '#ffffff' if s in ('body', '.app') else '', 'text': f'Count: {count}' if s == 'p' else '+1' if s == 'button' else '', 'ancestors': []} for i, s in enumerate(['html', 'body', '.app', 'p', 'button'])]
            regions = [{'label': label, 'id': [2, 4, 5][i], 'bounds': {'x': 10, 'y': 10+30*i, 'width': 100, 'height': 20}} for i, label in enumerate(['body', f'Count: {count}', '+1'])]
            regions[0]['bounds'] = {'x': 0, 'y': 0, 'width': 800, 'height': 600}
            app = {'id': 3, 'bounds': {'x': 24, 'y': 24, 'width': 800, 'height': 112}}
            context = {'pid': 123, 'count': count, 'at': 100, 'frames': 4+count*4, 'window': row['window'], 'probe': {'pid': 123, 'windowId': 5, 'scale': 1}, 'styles': styles, 'regions': regions, 'app': app}
            samples = []
            for i, region in enumerate(regions):
                box = region['bounds']; area = box['width'] * box['height']
                samples.append({**region, 'device': {'left': box['x'], 'top': box['y'], 'right': box['x']+box['width'], 'bottom': box['y']+box['height']},
                                'white': area-50, 'dark': 50, 'area': area, 'inkSHA256': str(count if i == 1 else 0)*64})
            shot = {'pid': 123, 'count': count, 'scale': 1, 'width': 800, 'height': 600, 'pngSHA256': original.sha((self.out / f'count-{count}.png').read_bytes()), 'samples': samples,
                    'app': app, 'appOverflow': {'left': 0, 'top': 0, 'right': 24, 'bottom': 0}}
            self.contexts.append(context); self.shots.append(shot)
        original.save(self.out, 'initial-styles', {'case': 'control', 'pid': 123, 'styles': self.contexts[0]['styles']})

    def tearDown(self):
        fixtures.Acceptance.tearDown(self)

    def contrast_validate(self, contexts=None, shots=None):
        original.save(self.out, 'observations', self.rows)
        for count, (context, shot) in enumerate(zip(contexts or self.contexts, shots or self.shots)):
            original.save(self.out, f'count-{count}-contrast-before', context)
            original.save(self.out, f'count-{count}-contrast-after', context)
            original.save(self.out, f'count-{count}-contrast-pixels', shot)
        runner.validate_control(self.out)

    def test_positive_contrast_receipt(self): self.contrast_validate()

    def test_measured_body_receipt_preserves_overflow_and_rejects_wrong_body_label_or_app_diagnostic(self):
        self.contrast_validate()
        self.assertEqual(self.shots[0]['samples'][0]['label'], 'body')
        self.assertEqual(self.shots[0]['app']['bounds']['x'] + self.shots[0]['app']['bounds']['width'], 824)
        self.assertEqual(self.shots[0]['appOverflow']['right'], 24)
        mutations = [lambda c, s: c[0]['regions'].pop(0), lambda c, s: c[0]['regions'][0].update(id=3),
                     lambda c, s: c[0]['regions'][0]['bounds'].update(width=801), lambda c, s: c[0]['regions'][0]['bounds'].update(x=-1),
                     lambda c, s: c[0]['regions'][0].update(label='app'), lambda c, s: c[0]['styles'][1].update(background=''),
                     lambda c, s: c[0]['regions'][1]['bounds'].update(x=750), lambda c, s: c[0]['regions'][2]['bounds'].update(y=590),
                     lambda c, s: c[0].pop('app'), lambda c, s: c[0]['app'].update(id=99),
                     lambda c, s: s[0]['appOverflow'].update(right=0), lambda c, s: s[0]['samples'][0].update(white=0)]
        for index, mutate in enumerate(mutations):
            contexts, shots = copy.deepcopy(self.contexts), copy.deepcopy(self.shots)
            mutate(contexts, shots)
            with self.subTest(fault=index), self.assertRaises((RuntimeError, KeyError, IndexError)):
                self.contrast_validate(contexts, shots)

    def test_missing_wrong_style_scene_scale_region_foreground_or_stale_pixels_reject(self):
        for mutate in [lambda c, s: c[0]['styles'].clear(), lambda c, s: c[0]['styles'][1].update(background=''), lambda c, s: c[0]['styles'][3].update(text='wrong'), lambda c, s: s[0].update(scale=2), lambda c, s: c[0]['probe'].update(pid=999), lambda c, s: c[0].update(frames=0), lambda c, s: s[0]['samples'].clear(), lambda c, s: s[0]['samples'][1].update(dark=0), lambda c, s: s[0]['samples'][1].update(white=0), lambda c, s: s[1]['samples'][1].update(inkSHA256=s[0]['samples'][1]['inkSHA256'])]:
            c, s = copy.deepcopy(self.contexts), copy.deepcopy(self.shots); mutate(c, s)
            with self.assertRaises((RuntimeError, KeyError, IndexError)): self.contrast_validate(c, s)
        self.contrast_validate(); (self.out / 'count-1-contrast-pixels.json').unlink()
        with self.assertRaises(FileNotFoundError): runner.validate_control(self.out)


if __name__ == '__main__': unittest.main()
