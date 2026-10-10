"""Fail-closed local preparation; one audited Python-only test module.

All steps and sources are audited before the first preparation child.
Never runs Bun, native tests, sample, keyboard, Metal or unfiltered discovery.
"""
import sys
sys.dont_write_bytecode = True
import argparse
import json
import os
from pathlib import Path
import subprocess
import measure as m

SAFE_PROGRAMS = {
    'success': 'import time; print("harmless-owned",flush=True); time.sleep(.15)',
    'failure': 'raise SystemExit(17)',
    'hang': 'import time; time.sleep(20)',
    'descendant': 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c","import time; time.sleep(20)"]); print("owned-descendant",flush=True); time.sleep(20)',
}


def harmless(argv):
    m.require(len(argv) == 3 and argv[:2] == [sys.executable, '-c'] and argv[2] in SAFE_PROGRAMS.values(), 'non-allowlisted preparation child')
    return {'argv': argv, 'sourceSHA256': m.sha(argv[2].encode()), 'classification': 'harmless Python, not native evidence'}


def audit(plan, head):
    expected = default_plan()
    m.require(plan == expected, 'unsafe file/argv/filter/extra-step selection; zero children')
    m.require(head == os.environ.get('GITHUB_SHA'), 'preparation wrong head')
    identity = m.source_identity()
    for name, value in identity['driverHashes'].items():
        frozen = subprocess.check_output(['git', 'show', head + ':' + name], cwd=m.ROOT, timeout=10)
        m.require(m.sha(frozen) == value, 'preparation source differs from committed review candidate')
    return {'allStepsAuditedBeforeSpawn': True, 'head': head, 'plan': plan, 'source': identity,
            'harmlessPrograms': {key: m.sha(value.encode()) for key, value in SAFE_PROGRAMS.items()},
            'preparationExecutableHashes': {step[0]: m.sha(Path(step[0]).resolve().read_bytes()) for step in plan}}


def default_plan():
    return [[sys.executable, '-m', 'unittest', 'discover', '-s', str(m.HERE), '-p', 'controls_test.py', '-v'],
            ['/opt/homebrew/bin/actionlint', '.github/workflows/owned-native32-stack-measurement.yml']]


def execute(out, head, plan=None):
    m.require(not out.is_relative_to(m.ROOT), 'preparation evidence must be outside checkout')
    out.mkdir(parents=True, exist_ok=False)
    result = {'rawExit': None, 'spawned': False, 'classification': 'safe synthetic/harmless controls only', 'start': m.stamp()}
    try:
        plan = plan if plan is not None else default_plan()
        evidence = audit(plan, head)  # ALL steps/source identities, before ANY test child.
        m.save(out, 'audit', evidence)
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
        result['steps'] = []
        for role, argv in zip(('controls', 'workflow-lint'), plan):
            child = m.Owned(argv, out, role, env)
            result['spawned'] = True
            try:
                child.child.wait(timeout=50)
            except subprocess.TimeoutExpired:
                result['timeout'] = role
            finally:
                receipt = child.close()
                m.save(out, role + '-process', receipt)
                result['rawExit'] = receipt['rawExit']
                result['steps'].append(receipt)
            m.require(receipt['rawExit'] == 0 and receipt['cleanupVerified'] and not result.get('timeout'), 'safe controls failed or cleanup unverified')
        m.require(all(m.sha(Path(path).resolve().read_bytes()) == value for path, value in evidence['preparationExecutableHashes'].items()), 'preparation executable identity drift')
    except BaseException as error:
        result['error'] = repr(error)
    finally:
        result['end'] = m.stamp()
        m.save(out, 'result', result)
        m.save(out, 'manifest', {str(p.relative_to(out)): m.sha(p.read_bytes()) for p in out.rglob('*') if p.is_file()})
    return 0 if result['rawExit'] == 0 and not result.get('error') else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--expected-head', required=True)
    args = parser.parse_args()
    raise SystemExit(execute(args.out.resolve(), args.expected_head))
