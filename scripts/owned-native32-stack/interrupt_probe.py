"""Audited real SIGTERM controls of owned preparation and harmless children only."""
import sys
sys.dont_write_bytecode = True
import argparse
import copy
import json
import os
from pathlib import Path
import signal
import time
import measure as m
import prepare as safe


def descendants(rows, root):
    found = {root}
    for _ in range(len(rows)):
        added = {pid for pid, row in rows.items() if row['ppid'] in found} - found
        if not added:
            break
        found |= added
    return {pid: m.token(rows[pid]) for pid in found if pid in rows
            and not rows[pid]['state'].startswith('Z') and not rows[pid]['command'].startswith('(')}


def probe(out, head):
    m.require(not out.is_relative_to(m.ROOT), 'probe evidence must be outside checkout')
    out.mkdir(parents=True, exist_ok=False)
    stages = ('controls', 'hang', 'descendant')
    planned = {stage: [sys.executable, str(m.HERE / 'prepare.py'), '--expected-head', head,
                       '--out', str(out / stage / 'preparation')] for stage in stages}
    audit = safe.audit(safe.default_plan(), head)
    m.require(all(argv[:4] == [sys.executable, str(m.HERE / 'prepare.py'), '--expected-head', head]
                  and argv[4] == '--out' and len(argv) == 6 for argv in planned.values()), 'unsafe probe argv')
    m.save(out, 'all-before-spawn-audit', {'canonicalAudit': audit, 'planned': planned,
                                         'classification': 'real harmless interruption only; no native/sample'})
    result = {'head': head, 'passed': False, 'stages': [], 'classification': 'harmless SIGTERM only'}
    try:
        for stage in stages:
            case = out / stage
            case.mkdir(exist_ok=False)
            child = None
            evidence = {'stage': stage, 'known': {}, 'passed': False}
            try:
                child = m.Owned(planned[stage], case, 'supervisor', dict(os.environ))
                evidence['supervisorInitial'] = copy.deepcopy(child.receipt)
                prep = case / 'preparation'
                started = prep / 'controls-started.json' if stage == 'controls' else (
                    prep / 'control-cases/test_real_harmless_timeout_and_known_descendant_cleanup/evidence' / (stage + '-started.json'))
                until = time.monotonic() + 30
                while child.child.poll() is None and time.monotonic() < until:
                    rows = m.table()
                    live = rows.get(child.child.pid)
                    m.require(live and m.token(live) == m.token(child.receipt['identity']), 'stale supervisor before probe')
                    observed = descendants(rows, child.child.pid)
                    m.require(all(pid not in evidence['known'] or evidence['known'][pid] == token
                                  for pid, token in observed.items()), 'reused owned identity during probe')
                    evidence['known'].update(observed)
                    if started.exists():
                        packet = json.loads(started.read_text())
                        target = rows.get(packet['identity']['pid']) if packet['identity'] else None
                        if target and not target['state'].startswith('Z') and m.token(target) == m.token(packet['identity']):
                            m.require(target['pid'] in evidence['known'], 'started fixture is not an owned descendant')
                            expected = safe.default_plan()[0] if stage == 'controls' else [sys.executable, '-c', safe.SAFE_PROGRAMS[stage]]
                            m.require(packet['argv'] == expected, 'non-allowlisted interruption target')
                            nested = descendants(rows, target['pid'])
                            if stage != 'descendant' or len(nested) >= 2:
                                evidence['targetBefore'] = target
                                evidence['targetChildrenBefore'] = nested
                                break
                    time.sleep(.005)
                m.require('targetBefore' in evidence, 'required active harmless stage not observed; no signal')
                rows = m.table()
                root = rows.get(child.child.pid)
                target = rows.get(evidence['targetBefore']['pid'])
                m.require(child.child.poll() is None and root and m.token(root) == m.token(child.receipt['identity']), 'stale supervisor at signal delivery')
                m.require(target and not target['state'].startswith('Z')
                          and m.token(target) == m.token(evidence['targetBefore']), 'harmless target no longer active at delivery')
                evidence['deliverySnapshot'] = {'supervisor': root, 'target': target, 'observed': m.stamp()}
                m.save(case, 'before-signal', evidence)
                os.kill(child.child.pid, signal.SIGTERM)
                child.receipt['signals'].append('SIGTERM')
                evidence['signalDelivery'] = {'kind': 'exact owned PID', 'pid': child.child.pid, 'delivered': m.stamp()}
                child.child.wait(timeout=20)
                evidence['supervisorRawExit'] = child.child.returncode
                m.require(child.child.returncode == 143, 'cancelled supervisor raw exit not preserved')
                saved = json.loads((prep / 'result.json').read_text())
                m.require(saved['interruptSignal'] == 15 and 'Cancelled' in saved['error'], 'interruption receipt missing')
                step = json.loads((prep / 'controls-process.json').read_text())
                # A step can finish naturally while supervisor cleanup starts.
                # Preserve raw0 instead of fabricating a signal-induced exit.
                m.require(step['rawExit'] in (0, 143, -15) and step['cleanupVerified'], 'control raw exit/cleanup not preserved')
                nested_cleanup = json.loads((prep / 'controls-nested-cleanup.json').read_text())
                m.require(nested_cleanup['cleanupVerified'] and nested_cleanup['survivors'] == [], 'nested cleanup unverified')
                rows = m.table()
                changed = [r for pid, r in rows.items() if pid in evidence['known'] and not r['state'].startswith('Z')]
                m.require(not changed, 'known descendant remains live or identity reused')
                evidence['knownLiveAfter'] = changed
                evidence['result'] = saved
                evidence['step'] = step
                evidence['nestedCleanup'] = nested_cleanup
                manifest = json.loads((prep / 'manifest.json').read_text())
                m.require(all(m.sha((prep / name).read_bytes()) == digest for name, digest in manifest.items()), 'interrupted manifest changed')
                for name in manifest:
                    if name.endswith('.json'):
                        json.loads((prep / name).read_text())
                m.require(not list(prep.rglob('*.pending-*')), 'receipt publication incomplete')
                evidence['manifestEntriesVerified'] = len(manifest)
                evidence['passed'] = True
            finally:
                if child:
                    evidence['supervisorCleanup'] = child.close()
                    m.save(case, 'supervisor-process', evidence['supervisorCleanup'])
                # No arbitrary PID cleanup: only exact owned tokens observed above.
                evidence['fallbackKnownCleanup'] = safe.cleanup_nested(evidence['known'])
                m.save(case, 'probe', evidence)
                result['stages'].append(evidence)
            m.require(evidence['passed'] and evidence['fallbackKnownCleanup']['cleanupVerified'], 'interruption control failed')
        m.require(m.source_identity() == audit['source'], 'probe source identity changed')
        result['passed'] = True
    except BaseException as error:
        result['error'] = repr(error)
    finally:
        m.save(out, 'result', result)
        m.save(out, 'manifest', {str(p.relative_to(out)): m.sha(p.read_bytes()) for p in out.rglob('*') if p.is_file()})
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    safe.install_cancellation()
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-head', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(probe(args.out.resolve(), args.expected_head))
