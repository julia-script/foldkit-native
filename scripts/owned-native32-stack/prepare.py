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
    expected = [sys.executable, '-m', 'unittest', 'discover', '-s', str(m.HERE), '-p', 'controls_test.py', '-v']
    m.require(plan and all(step == expected for step in plan) and len(plan) == 1, 'unsafe file/argv/filter/extra-step selection; zero children')
    m.require(head == os.environ.get('GITHUB_SHA'), 'preparation wrong head')
    identity = m.source_identity()
    for name, value in identity['driverHashes'].items():
        frozen = subprocess.check_output(['git', 'show', head + ':' + name], cwd=m.ROOT, timeout=10)
        m.require(m.sha(frozen) == value, 'preparation source differs from committed review candidate')
    return {'allStepsAuditedBeforeSpawn': True, 'head': head, 'plan': plan, 'source': identity,
            'harmlessPrograms': {key: m.sha(value.encode()) for key, value in SAFE_PROGRAMS.items()}}


def execute(out, head, plan=None):
    m.require(not out.is_relative_to(m.ROOT), 'preparation evidence must be outside checkout')
    out.mkdir(parents=True, exist_ok=False)
    result = {'rawExit': None, 'spawned': False, 'classification': 'safe synthetic/harmless controls only', 'start': m.stamp()}
    try:
        plan = plan if plan is not None else [[sys.executable, '-m', 'unittest', 'discover', '-s', str(m.HERE), '-p', 'controls_test.py', '-v']]
        evidence = audit(plan, head)  # ALL steps/source identities, before ANY test child.
        m.save(out, 'audit', evidence)
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
        child = m.Owned(plan[0], out, 'controls', env)
        result['spawned'] = True
        try:
            child.child.wait(timeout=50)
        except subprocess.TimeoutExpired:
            result['timeout'] = True
        receipt = child.close()
        m.save(out, 'controls-process', receipt)
        result['rawExit'] = receipt['rawExit']
        m.require(receipt['rawExit'] == 0 and receipt['cleanupVerified'] and not result.get('timeout'), 'safe controls failed or cleanup unverified')
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
