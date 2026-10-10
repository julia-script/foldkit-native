"""Corrected-target source/receipt pipeline controls; no native execution."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import corrected_run as runner
import contrast_run as contrast
import contrast_run_test as controls


class Corrected(unittest.TestCase):
    def test_entrypoint_selects_one_corrected_case_and_preserves_exception_identity(self):
        root, out = Path('/source'), Path('/evidence')
        with patch.object(runner.original, 'run', return_value='receipt') as run:
            self.assertEqual(runner.run(root, out), 'receipt')
            run.assert_called_once_with(root, out, case='corrected')
        error = RuntimeError('unchanged native error')
        with patch.object(runner.original, 'run', side_effect=error):
            with self.assertRaises(RuntimeError) as caught: runner.run(root, out)
            self.assertIs(caught.exception, error)

    def test_corrected_receipts_keep_all_pixel_input_and_disposal_guards(self):
        fixture = controls.ControlReceipt()
        fixture.setUp()
        try:
            controls.fixtures.Acceptance.validate(fixture)
            fixture.contrast_validate()
            packet = json.loads((fixture.out / 'initial-styles.json').read_text())
            packet['case'] = 'corrected'
            runner.original.save(fixture.out, 'initial-styles', packet)
            runner.original.validate(fixture.out)
            contrast.validate_control(fixture.out, 'corrected')
            for label in ['original', 'control', 'unknown']:
                packet['case'] = label
                runner.original.save(fixture.out, 'initial-styles', packet)
                with self.assertRaisesRegex(RuntimeError, 'styles'): contrast.validate_control(fixture.out, 'corrected')
            with self.assertRaisesRegex(RuntimeError, 'styles'): contrast.validate_control(fixture.out, 'unknown')
        finally: fixture.tearDown()

    def test_real_pipeline_uses_verbatim_source_strict_receipts_and_preserves_raw_failure(self):
        # Synthetic receipt fixtures and mocked launch boundaries, never GPUI.
        original = runner.original
        for fail in [False, True]:
            fixture = controls.ControlReceipt(); fixture.setUp()
            try:
                controls.fixtures.Acceptance.validate(fixture); fixture.contrast_validate()
                packet = json.loads((fixture.out / 'initial-styles.json').read_text()); packet['case'] = 'corrected'
                original.save(fixture.out, 'initial-styles', packet)
                source = "const css = 'body, .app { background-color: #ffffff; color: #000000; }';\nconst app = " + contrast.MOUNT
                source_receipt = {'head': original.HEAD, 'clean': '', 'readmeSHA256': original.README, 'lockSHA256': original.LOCK, 'snippetSHA256': original.sha(source.encode())}
                driver = {'head': 'd'*40, 'clean': '', 'files': {'witness.ts': 'a'*64}}
                dependencies = {'packages': {name: {'name': name, 'version': version, 'packageSHA256': 'a'*64} for name, version in original.PINS.items()},
                                'binary': {'package': '@gpuix/native-darwin-arm64', 'version': '0.10.0', 'file': 'mock.node', 'sha256': 'a'*64}}
                calls = []
                with tempfile.TemporaryDirectory() as directory:
                    out = Path(directory)
                    def command(argv, _root, env, destination, name, timeout):
                        calls.append(name)
                        self.assertEqual(env['QUALIFICATION_CASE'], 'corrected')
                        if name == 'node-version': return 'v22.22.3'
                        if name == 'bun-version': return '1.3.9'
                        if name == 'compile-frame-probe': Path(argv[-1]).write_bytes(b'synthetic probe'); return ''
                        if name == 'native-readme':
                            self.assertEqual(argv[2], f'import {{ begin as qualificationBegin }} from {json.dumps(str(original.HERE / "witness.ts"))};\nconst qualificationObserve = qualificationBegin();\n' + source + '\nawait qualificationObserve(app);\n')
                            self.assertEqual(timeout, 45)
                            for p in fixture.out.iterdir():
                                if p.is_file(): (destination / p.name).write_bytes(p.read_bytes())
                            if fail:
                                child = json.loads((destination / 'native-readme.json').read_text())
                                child.update(exit=17, status='command-failed')
                                original.save(destination, 'native-readme', child)
                                raise RuntimeError('native-readme: command-failed; original exit 17')
                        return ''
                    with patch.object(original.platform, 'system', return_value='Darwin'), patch.object(original.platform, 'machine', return_value='arm64'), \
                         patch.object(original, 'driver_source', return_value=driver), patch.object(original, 'source', return_value=(source_receipt, source)), \
                         patch.object(original, 'dependency_identity', return_value=dependencies), patch.object(original, 'command', side_effect=command), \
                         patch.object(contrast, 'control_source', side_effect=AssertionError('CSS injection forbidden')) as inject:
                        if fail:
                            with self.assertRaisesRegex(RuntimeError, 'original exit 17'): runner.run(out, out)
                        else: runner.run(out, out)
                        inject.assert_not_called()
                    self.assertEqual(calls.count('native-readme'), 1)
                    result = json.loads((out / 'result.json').read_text())
                    self.assertEqual(result['case'], 'corrected')
                    self.assertEqual(result['status'], 'failed' if fail else 'passed')
                    self.assertEqual((out / 'original-snippet.ts').read_text(), source)
                    self.assertFalse((out / 'control-snippet.ts').exists())
                    self.assertEqual(json.loads((out / 'native-readme.json').read_text())['exit'], 17 if fail else 0)
                    self.assertIn('native-readme.json', json.loads((out / 'manifest.json').read_text()))
            finally: fixture.tearDown()


if __name__ == '__main__': unittest.main()
