"""Fail-closed LOCAL preparation selections. Never a native test runner.

Every entrypoint and owned harmless child is audited before spawn. Fingerprints
pin the reviewed Python closure and static-check inputs, not native correctness.
"""
import sys
sys.dont_write_bytecode = True
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
CHECKS = {
    'controls': (sys.executable, str(ROOT / 'scripts/scroll-repair-qualification/controls.py')),
    'policy': (sys.executable, str(ROOT / 'scripts/scroll-repair-qualification/controls.py'), 'Selection'),
    'install': ('npm', 'ci', '--ignore-scripts'),
    'typecheck': ('bun', 'run', 'typecheck'),
    'css': ('bun', 'run', 'css', '--check'),
    'diff': ('git', 'diff', '--check'),
    'workflow': ('actionlint', '.github/workflows/scroll-repair-qualification.yml'),
    'ast': (sys.executable, '-c', "import ast;from pathlib import Path;[ast.parse(p.read_text(),filename=str(p)) for p in Path('scripts/scroll-repair-qualification').glob('*.py')];print('Five Python ASTs pass')"),
    'bun-pin': ('bun', '--version'),
    'node-pin': ('node', '--version'),
}
HARMLESS = {
    'success': "print('owned harmless success')",
    'failure': "print('owned harmless failure');raise SystemExit(7)",
    'timeout': "import time;time.sleep(5)",
    'grandchild': "import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(5)']);print('owned harmless parent')",
    'bytecode': "import contract,sys;assert sys.dont_write_bytecode;print('owned harmless import')",
}
# Generated from inspected files; final source review separately pins this policy.
FINGERPRINTS = {'scripts/scroll-repair-qualification/contract.py': '14cebf552b70f1b02c182c9c400abae1051147e26ed69302ffeee92e6b405c84', 'scripts/scroll-repair-qualification/run.py': '5991c69c30897579848c300ca3f5d5cc35f735e9ec9e4e8983e0a0872ecbb466', 'scripts/scroll-repair-qualification/supervise.py': '25d1d22ef5cd087509940ca16293bc8530410e34f9a542fc11132f6c3ba7abe9', 'scripts/scroll-repair-qualification/controls.py': '08a548cba3a707266a7d3a9961d5ac6d160213debbec1e2cd4529c5b104bdd04', '.github/workflows/scroll-repair-qualification.yml': 'a5778750237370a48a5a17e85d16ce10611dbb4210a17cbc819203cc8e39ebb7', 'examples/big-list/native.test.ts': '3f80e7c97d833e4984a7c890bd25b5a0dad47877f74a1b666f5e2c4ee28a99ec', 'examples/pixel-art/native.test.ts': '2429f96ce5e1d44dd2c184172d41d4bb65c98e0faeb30fee6cff8b6c152ce167', 'packages/foldkit-gpuix/test/support.ts': '9611e874ca4e1954030c22125d896b5f195197ac2abebab041cf7a730c1d78ee', 'examples/support/harness.ts': 'dab0eb03569be919bc71e4196c3ff8046ce8eeda1f9339f77d816c58ffe18c18', 'test/support/dom.ts': '4c199816d8e8803091398c71264655cc4daf555801d9ec946c2d82985e018c0c', 'bunfig.toml': '2b9303e9892be8e3aada7a5036d17d9d13820cb34878eaf527927d337d014bba', 'package.json': 'f66e710d975d40a2ba81fe8f75518a70c4a15bfb54d144b8d12db05c4763ecbf', 'package-lock.json': '93535904b69ec9f551e120066edca66d3488d5969f6b3c3fb464474a583181c8', 'scripts/css.ts': 'ec58287e7a1ff1e0f9ced37220da50c9dfccf74eb7235de199255224576b90e8', 'tsconfig.json': '36536b15f23a96605cf4fa26bec51253669eda2018c6c00e6b8feb935b025951'}


class Rejected(ValueError):
    pass


def audit_sources(root, environment):
    root = Path(root).resolve()
    if any(environment.get(name) for name in ('NODE_OPTIONS', 'BUN_OPTIONS', 'BUN_PRELOAD', 'PYTHONPATH', 'PYTHONSTARTUP')):
        raise Rejected('Unreviewed runtime injection')
    if not FINGERPRINTS:
        raise Rejected('Inspected source manifest missing')
    for name, expected in FINGERPRINTS.items():
        path = root / name
        if not path.is_file() or not path.resolve().is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise Rejected('Missing, changed or escaped inspected source: ' + name)
    return root


def audit(root, command, environment):
    if tuple(command) not in CHECKS.values():
        raise Rejected('Outside the exact non-native preparation allowlist')
    root = audit_sources(root, environment)
    return {'accepted': True, 'command': list(command), 'root': str(root), 'auditedFiles': len(FINGERPRINTS), 'nativeExecuted': False}


def audit_child(root, command, environment, kind, owned=None):
    """Only fixed harmless programs or the rejecting ordinary driver entrypoint."""
    command = list(command)
    if kind in HARMLESS:
        if command != [sys.executable, '-c', HARMLESS[kind]]:
            raise Rejected('Harmless program bytes changed')
    elif kind == 'clone':
        if owned is None or command != ['git', 'clone', '--shared', '--quiet', str(ROOT), str(owned)]:
            raise Rejected('Owned local clone command changed')
        target = Path(owned).resolve()
        if target.exists() or target.is_relative_to(ROOT) or not target.parent.name.startswith('test_real_strict_'):
            raise Rejected('Clone must use the exclusive harmless control owner')
    elif kind == 'startup':
        if owned is None or command != [sys.executable, str(ROOT / 'scripts/scroll-repair-qualification/run.py'), '--out', str(owned)]:
            raise Rejected('Ordinary startup command changed')
        target = Path(owned).resolve()
        if target.exists() or target.is_relative_to(ROOT) or environment.get('GITHUB_ACTIONS'):
            raise Rejected('Startup rejection control must use a fresh external owner without hosted authority')
    else:
        raise Rejected('Unknown preparation child kind')
    root = audit_sources(root, environment)
    return {'accepted': True, 'kind': kind, 'command': command, 'root': str(root), 'nativeExecuted': False}


def execute(root, command, environment, spawn=subprocess.run):
    receipt = audit(root, command, environment)
    argv = list(command) if command[0] == sys.executable else ['rtk', 'proxy', *command]
    return spawn(argv, cwd=receipt['root'], env=dict(environment), shell=False, check=False)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('check', choices=CHECKS)
    parser.add_argument('--out')
    args = parser.parse_args()
    command = CHECKS[args.check]
    receipt = audit(ROOT, command, os.environ)
    if not args.out:
        print(json.dumps(receipt)); return 0
    out = Path(args.out).resolve()
    if out.is_relative_to(ROOT):
        raise Rejected('Local receipts must be outside source')
    out.mkdir(parents=True, exist_ok=False)
    (out / 'audit.json').write_text(json.dumps(receipt, indent=2) + '\n')
    from supervise import supervise
    argv = list(command) if command[0] == sys.executable else ['rtk', 'proxy', *command]
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'SCROLL_SAFE_RECEIPTS': str(out / 'children')}
    state = supervise(argv, out / 'raw.log', env, deadline=180)
    (out / 'supervision.json').write_text(json.dumps(state, indent=2) + '\n')
    from contract import cleaned
    return state['returncode'] if cleaned(state) and 0 <= state['returncode'] < 256 else 1


if __name__ == '__main__':
    raise SystemExit(main())
