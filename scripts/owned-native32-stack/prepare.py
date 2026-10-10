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
import signal
import subprocess
import time
import measure as m

REQUESTED_SIGNAL = None
RAISED_SIGNAL = False


class Cancelled(KeyboardInterrupt):
    def __init__(self, signum):
        self.signum = signum
        super().__init__('preparation signal ' + str(signum))


def check_cancellation():
    global RAISED_SIGNAL
    if REQUESTED_SIGNAL and not RAISED_SIGNAL and not m.DEFER_CANCELLATION:
        RAISED_SIGNAL = True
        raise Cancelled(REQUESTED_SIGNAL)


def install_cancellation():
    def interrupted(signum, _frame):
        global REQUESTED_SIGNAL
        if REQUESTED_SIGNAL is None:
            REQUESTED_SIGNAL = signum
        check_cancellation()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    m.CANCELLATION_CHECK = check_cancellation


def finish_active():
    receipts = []
    previous = m.DEFER_CANCELLATION
    m.DEFER_CANCELLATION = True
    try:
        for child in list(reversed(m.ACTIVE_OWNED)):
            receipt = child.close()
            path = child.out / (child.role + '-process.json')
            if not path.exists():
                m.save(child.out, child.role + '-process', receipt)
            else:
                m.require(json.loads(path.read_text()) == receipt, 'closed child receipt collision')
            receipts.append(receipt)
    finally:
        m.DEFER_CANCELLATION = previous
    return receipts


def observe_nested(child, known):
    rows = m.table()
    if child.child.poll() is not None:
        return rows
    root = rows.get(child.child.pid)
    m.require(root and child.receipt['identity'] and m.token(root) == m.token(child.receipt['identity']), 'unverified preparation step identity')
    parents = {child.child.pid}
    for _ in range(len(rows)):
        added = False
        for pid, row in rows.items():
            if row['ppid'] in parents and pid not in parents:
                m.require(pid not in known or known[pid] == m.token(row), 'reused nested preparation identity')
                known[pid] = m.token(row)
                parents.add(pid)
                added = True
        if not added:
            break
    return rows


def cleanup_nested(known):
    result = {'known': known, 'signals': [], 'cleanupVerified': False, 'survivors': None}
    try:
        for sig, wait in ((signal.SIGTERM, 2), (signal.SIGKILL, 2)):
            rows = m.table()
            for pid, expected in known.items():
                row = rows.get(pid)
                if row and not row['state'].startswith('Z'):
                    m.require(m.token(row) == expected, 'reused nested PID: signal refused')
                    try:
                        os.kill(pid, sig)
                        result['signals'].append({'pid': pid, 'signal': signal.Signals(sig).name})
                    except ProcessLookupError:
                        pass
            until = time.monotonic() + wait
            while True:
                rows = m.table()
                live = [r for pid, r in rows.items() if pid in known and not r['state'].startswith('Z')]
                m.require(all(m.token(r) == known[r['pid']] for r in live), 'reused nested PID: cleanup unverified')
                if not live or time.monotonic() >= until:
                    break
                time.sleep(.02)
            if not live:
                break
        result['survivors'] = live
        result['cleanupVerified'] = not live
        result['zombiesNotReapedBySupervisor'] = [r for pid, r in rows.items() if pid in known and r['state'].startswith('Z')]
    except BaseException as error:
        result['error'] = repr(error)
    return result


def close_step(child, nested, out, role, result):
    previous = m.DEFER_CANCELLATION
    m.DEFER_CANCELLATION = True
    try:
        try:
            observe_nested(child, nested)
        except BaseException as inspection:
            result['inspectionError'] = repr(inspection)
        m.save(out, role + '-nested-observed', nested)
        receipt = child.close(grace_seconds=12)
        m.save(out, role + '-process', receipt)
        cleanup = cleanup_nested(nested)
        m.save(out, role + '-nested-cleanup', cleanup)
        result['rawExit'] = receipt['rawExit']
        result['steps'].append(receipt)
        return receipt, cleanup
    finally:
        m.DEFER_CANCELLATION = previous

SAFE_PROGRAMS = {
    'success': 'import time; print("harmless-owned",flush=True); time.sleep(.15)',
    'failure': 'import time; time.sleep(.15); raise SystemExit(17)',
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
    return [[sys.executable, str(m.HERE / 'controls_test.py'), '-v'],
            ['/opt/homebrew/bin/actionlint', '.github/workflows/owned-native32-stack-measurement.yml']]


def execute(out, head, plan=None):
    m.require(not out.is_relative_to(m.ROOT), 'preparation evidence must be outside checkout')
    out.mkdir(parents=True, exist_ok=False)
    result = {'rawExit': None, 'spawned': False, 'classification': 'safe synthetic/harmless controls only', 'start': m.stamp()}
    try:
        plan = plan if plan is not None else default_plan()
        evidence = audit(plan, head)  # ALL steps/source identities, before ANY test child.
        m.save(out, 'audit', evidence)
        cases = out / 'control-cases'
        cases.mkdir(exist_ok=False)
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
               'FOLDKIT_NATIVE32_SAFE_CONTROLS_EVIDENCE': str(cases)}
        result['steps'] = []
        for role, argv in zip(('controls', 'workflow-lint'), plan):
            child = m.Owned(argv, out, role, env)
            result['spawned'] = True
            nested = {}
            try:
                until = time.monotonic() + 50
                while child.child.poll() is None:
                    if time.monotonic() >= until:
                        raise subprocess.TimeoutExpired(argv, 50)
                    observe_nested(child, nested)
                    time.sleep(.02)
            except subprocess.TimeoutExpired:
                result['timeout'] = role
            finally:
                receipt, nested_cleanup = close_step(child, nested, out, role, result)
            check_cancellation()
            m.require(receipt['rawExit'] == 0 and receipt['cleanupVerified'] and nested_cleanup['cleanupVerified']
                      and not result.get('timeout') and not result.get('inspectionError'), 'safe controls failed or cleanup unverified')
        m.require(all(m.sha(Path(path).resolve().read_bytes()) == value for path, value in evidence['preparationExecutableHashes'].items()), 'preparation executable identity drift')
    except BaseException as error:
        result['error'] = repr(error)
        if isinstance(error, Cancelled):
            result['interruptSignal'] = error.signum
    finally:
        previous = m.DEFER_CANCELLATION
        m.DEFER_CANCELLATION = True
        try:
            if REQUESTED_SIGNAL:
                result['interruptSignal'] = REQUESTED_SIGNAL
            result['end'] = m.stamp()
            m.save(out, 'result', result)
            if REQUESTED_SIGNAL and not result.get('interruptSignal'):
                m.save(out, 'late-interruption', {'signal': REQUESTED_SIGNAL, 'observed': m.stamp()})
            m.save(out, 'manifest', {str(p.relative_to(out)): m.sha(p.read_bytes()) for p in out.rglob('*') if p.is_file()})
        finally:
            m.DEFER_CANCELLATION = previous
    interrupted = result.get('interruptSignal') or REQUESTED_SIGNAL
    return 128 + interrupted if interrupted else 0 if result['rawExit'] == 0 and not result.get('error') else 1


if __name__ == '__main__':
    install_cancellation()
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--expected-head', required=True)
    args = parser.parse_args()
    try:
        code = execute(args.out.resolve(), args.expected_head)
    finally:
        finish_active()
    raise SystemExit(code)
