"""Real Python startup/git controls in disposable checkouts. No native boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class Startup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.repository = Path(__file__).resolve().parents[2]
        self.head = self.git(self.repository, 'rev-parse', 'HEAD').strip()
        self.root = self.checkout('driver', self.head)
        self.env = {**os.environ, 'GITHUB_SHA': self.head}
        for name in ('PYTHONDONTWRITEBYTECODE', 'PYTHONPYCACHEPREFIX', 'FOLDKIT_NATIVE_NO_WINDOW', 'NAPI_RS_NATIVE_LIBRARY_PATH'):
            self.env.pop(name, None)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, cwd, *args):
        return subprocess.check_output(['git', *args], cwd=cwd, text=True, stderr=subprocess.PIPE)

    def checkout(self, name, head):
        root = self.base / name
        subprocess.run(['git', 'clone', '--quiet', '--no-hardlinks', '--no-checkout', str(self.repository), str(root)],
                       check=True, capture_output=True, timeout=10)
        self.git(root, 'checkout', '--quiet', '--detach', head)
        return root

    def probe(self, entrypoint, out, main=False, target=None):
        # Force ordinary adjacent-cache policy, including on Apple's interpreter
        # whose default instead redirects bytecode outside the checkout.
        code = '''import sys,runpy,json,os
from pathlib import Path
root,entrypoint,out,target,main=sys.argv[1:]
sys.pycache_prefix=None
sys.path.insert(0,str(Path(root)/'scripts/readme-qualification'))
path=str(Path(root)/'scripts/readme-qualification'/entrypoint)
if main=='yes':
    # Exercise the real entrypoint/pipeline, but stop BEFORE the first non-git
    # subprocess can launch: no Node, npm, Swift, Bun or native evaluation.
    def boundary(event,args):
        if event=='subprocess.Popen' and Path(str(args[0])).name!='git':
            raise InterruptedError('safe boundary before any runtime/native command')
    sys.addaudithook(boundary)
    sys.argv=[path,target,out]
    runpy.run_path(path,run_name='__main__')
else:
    ns=runpy.run_path(path,run_name='startup_import_probe')
    module=ns.get('original')
    driver=module.driver_source if module else ns['driver_source']
    result=driver(Path(out),'probe')
    command=module.command if module else ns['command']
    owned=command([sys.executable,'-c','import sys;print(sys.dont_write_bytecode)'],Path(out),os.environ.copy(),Path(out),'owned-python',2)
    assert owned.strip()=='True',owned
    print(json.dumps({'driver':result,'bytecodeDisabled':sys.dont_write_bytecode,
                      'subprocessPolicy':os.environ.get('PYTHONDONTWRITEBYTECODE')}))
'''
        out.mkdir(parents=True, exist_ok=True)
        return subprocess.run([sys.executable, '-c', code, str(self.root), entrypoint, str(out), str(target or ''), 'yes' if main else 'no'],
                              cwd=self.root, env=self.env, text=True, capture_output=True, timeout=10)

    def test_real_owned_entrypoint_imports_stay_clean_without_startup_flags(self):
        for entrypoint in ('run.py', 'contrast_run.py'):
            with self.subTest(entrypoint=entrypoint):
                out = self.base / entrypoint
                child = self.probe(entrypoint, out)
                self.assertEqual(child.returncode, 0, child.stderr)
                receipt = json.loads(child.stdout)
                self.assertEqual(receipt['driver']['head'], self.head)
                self.assertEqual(receipt['driver']['clean'], '')
                self.assertTrue(receipt['bytecodeDisabled'])
                self.assertEqual(receipt['subprocessPolicy'], '1')
                self.assertEqual(self.git(self.root, 'status', '--porcelain', '--untracked-files=all'), '')
                self.assertEqual(list(self.root.rglob('*.pyc')), [])

    def test_evidence_writer_failure_keeps_original_guard_rejection(self):
        import runpy
        ns = runpy.run_path(str(self.repository / 'scripts/readme-qualification/run.py'), run_name='writer_fault_probe')
        with patch.object(ns['subprocess'], 'check_output', side_effect=[self.head, '?? unexpected.txt\n']), \
             patch.dict(os.environ, {'GITHUB_SHA': self.head}):
            # A file cannot contain driver-state evidence. Actual write raises
            # OSError, but the unchanged dirty-source rejection still escapes.
            blocked = self.base / 'not-a-directory'
            blocked.write_text('writer fault control')
            with self.assertRaisesRegex(RuntimeError, 'runner is not the clean reviewed event head'):
                ns['driver_source'](blocked, 'before')

    def test_full_dirty_and_wrong_head_state_rejects_without_waiver(self):
        for defect in ('tracked', 'untracked', 'wrong-head'):
            root = self.checkout(defect, self.head)
            self.root = root
            if defect == 'tracked':
                (root / 'scripts/readme-qualification/witness.ts').write_text('deliberate tracked drift')
            elif defect == 'untracked':
                extra = root / 'unexpected'
                extra.mkdir()
                (extra / 'a.txt').write_text('deliberate untracked file')
                (extra / 'b.txt').write_text('second untracked file')
            else:
                self.env['GITHUB_SHA'] = 'f' * 40
            out = self.base / ('evidence-' + defect)
            child = self.probe('contrast_run.py', out)
            with self.subTest(defect=defect):
                self.assertEqual(child.returncode, 1)
                self.assertIn('runner is not the clean reviewed event head', child.stderr)
                state = json.loads((out / 'driver-state-probe.json').read_text())
                self.assertEqual(state['head'], self.head)
                self.assertEqual(state['expectedGITHUB_SHA'], self.env['GITHUB_SHA'])
                self.assertEqual(state['clean'], self.git(root, 'status', '--porcelain', '--untracked-files=all'))
                if defect == 'untracked':
                    self.assertIn('?? unexpected/a.txt', state['clean'])
                    self.assertIn('?? unexpected/b.txt', state['clean'])
                self.assertEqual(list(root.rglob('*.pyc')), [])
            self.env['GITHUB_SHA'] = self.head

    def test_real_entrypoint_pipeline_retains_early_state_error_raw_exit_and_manifest(self):
        target = self.checkout('target', 'c4e4ced134f327c143d50dea3c03774fa28738d0')
        for entrypoint in ('run.py', 'contrast_run.py'):
            self.env['GITHUB_SHA'] = 'f' * 40
            out = self.base / ('failed-' + entrypoint)
            child = self.probe(entrypoint, out, main=True, target=target)
            case = out / 'original' if entrypoint == 'contrast_run.py' else out
            with self.subTest(entrypoint=entrypoint):
                self.assertEqual(child.returncode, 1)
                self.assertIn('runner is not the clean reviewed event head', child.stderr)
                result = json.loads((case / 'result.json').read_text())
                self.assertEqual(result['case'], 'original')
                self.assertEqual(result['status'], 'failed')
                self.assertIn('runner is not the clean reviewed event head', result['error'])
                for phase in ('before', 'after'):
                    state = json.loads((case / f'driver-state-{phase}.json').read_text())
                    self.assertEqual(state['head'], self.head)
                    self.assertEqual(state['expectedGITHUB_SHA'], 'f' * 40)
                    self.assertEqual(state['clean'], '')
                    self.assertTrue(state['bytecodeDisabled'])
                    self.assertIn(f'driver-state-{phase}.json', json.loads((case / 'manifest.json').read_text()))
                self.assertFalse((out / 'control').exists())
                self.assertFalse((case / 'node-version.json').exists())
                self.assertFalse((case / 'native-readme.json').exists())

    def test_real_clean_pipeline_reaches_only_safe_runtime_boundary(self):
        target = self.checkout('target', 'c4e4ced134f327c143d50dea3c03774fa28738d0')
        for entrypoint in ('run.py', 'contrast_run.py'):
            out = self.base / ('clean-' + entrypoint)
            child = self.probe(entrypoint, out, main=True, target=target)
            case = out / 'original' if entrypoint == 'contrast_run.py' else out
            with self.subTest(entrypoint=entrypoint):
                self.assertEqual(child.returncode, 1)
                self.assertIn('safe boundary before any runtime/native command', child.stderr)
                result = json.loads((case / 'result.json').read_text())
                self.assertNotIn('sourceError', result)
                self.assertIn('safe boundary', result['error'])
                before = json.loads((case / 'driver-before.json').read_text())
                self.assertEqual(before, json.loads((case / 'driver-after.json').read_text()))
                self.assertEqual(before['clean'], '')
                command = json.loads((case / 'node-version.json').read_text())
                self.assertEqual(command['status'], 'spawn-failed')
                self.assertNotIn('pid', command)
                self.assertFalse((case / 'native-readme.json').exists())
                self.assertFalse((out / 'control').exists())
                self.assertEqual(list(self.root.rglob('*.pyc')), [])


if __name__ == '__main__':
    unittest.main()
