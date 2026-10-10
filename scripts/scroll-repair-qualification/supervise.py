"""Bounded owned groups, including nested-session cancellation and durable receipts."""
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid


class Cancelled(KeyboardInterrupt):
    def __init__(self, signum):
        self.signum = signum
        super().__init__('supervisor cancelled by ' + signal.Signals(signum).name)


def durable(path, value, exclusive=False):
    """Only a fresh owner may write; final receipts are exclusive, snapshots atomic."""
    data = json.dumps(value, indent=2) + '\n'
    if exclusive:
        with path.open('x') as file:
            file.write(data); file.flush(); os.fsync(file.fileno())
    else:
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        with temporary.open('x') as file:
            file.write(data); file.flush(); os.fsync(file.fileno())
        os.replace(temporary, path)


@contextlib.contextmanager
def cancellation():
    """Between supervised children cancellation stops callers, never starts more work."""
    def stop(signum, _frame):
        raise Cancelled(signum)
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def group_members(pgid):
    rows = subprocess.check_output(['ps', '-axo', 'pid=,pgid=,stat='], text=True, timeout=5)
    return [int(pid) for pid, group, status in (row.split() for row in rows.splitlines())
            if int(group) == pgid and not status.startswith('Z')]


def process_identity(pid):
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'pid=,pgid=,lstart='], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
    row = result.stdout.strip()
    if result.returncode not in (0, 1) or result.returncode == 1 and row:
        raise RuntimeError('process identity query failed')
    if not row:
        return None
    process, group, birth = row.split(maxsplit=2)
    return {'pid': int(process), 'pgid': int(group), 'birth': birth}


def registered_descendants(registry, initial_pid):
    if registry is None:
        return []
    entries = [json.loads(path.read_text()) for path in registry.glob('*.json')]
    known, found = {initial_pid}, []
    while True:
        fresh = [entry for entry in entries if entry['ownerPid'] in known and entry['pid'] not in known]
        if not fresh:
            return found
        for entry in fresh:
            if entry['pid'] != entry['pgid'] or entry['identity']['pid'] != entry['pid'] or entry['identity']['pgid'] != entry['pgid']:
                raise RuntimeError('nested ownership registry identity malformed')
            found.append(entry); known.add(entry['pid'])


def supervise(command, raw_path, env, deadline=60, state=None):
    state = {} if state is None else state
    raw_path = Path(raw_path)
    owner_path = raw_path.with_name(raw_path.name + '.ownership.json')
    final_path = raw_path.with_name(raw_path.name + '.supervision.json')
    events_path = raw_path.with_name(raw_path.name + '.events.jsonl')
    if owner_path.exists() or final_path.exists() or events_path.exists() or raw_path.exists() and raw_path.stat().st_size:
        raise FileExistsError('refusing to overwrite prior supervisor evidence')
    started = time.monotonic()
    state.update(started=False, returncode=None, reaped=False, groupClean=False, remainingGroupMembers=None,
                 naturalExit=False, deadlineExceeded=False, signals=[], command=command, envelopeSeconds=deadline,
                 cancelledBy=None, ownerPid=os.getpid())
    child, failure, inspection_failure, registry = None, None, None, None
    members, requested, descendants, nested_clean = None, [], [], False
    previous = {}

    def publish(stage):
        durable(owner_path, {'stage': stage, **state})
        with events_path.open('a') as file:
            file.write(json.dumps({'stage': stage, **state}) + '\n'); file.flush(); os.fsync(file.fileno())

    def request(signum, _frame):
        # Defer until ownership is durable. This covers the Popen-return window
        # without masking signals inherited by the newly created child.
        if not requested:
            requested.append(signum)
            state['cancelledBy'] = signal.Signals(signum).name

    def inspect():
        nonlocal inspection_failure
        try:
            return group_members(child.pid)
        except BaseException as error:
            inspection_failure = {'type': type(error).__name__, 'message': str(error)[:2000]}
            return None

    def send(pgid, sig, identity=None):
        if identity is not None:
            current = process_identity(identity['pid'])
            if current is not None and current != identity:
                raise RuntimeError('refusing to signal a reused nested PID/PGID')
        try:
            os.killpg(pgid, sig)
            state['signals'].append({'pgid': pgid, 'signal': signal.Signals(sig).name})
        except ProcessLookupError:
            pass
        except PermissionError:
            if group_members(pgid) != []:
                raise

    try:
        previous = {sig: signal.signal(sig, request) for sig in (signal.SIGTERM, signal.SIGINT)}
        publish('before-spawn')
        if requested:
            raise Cancelled(requested[0])
        if env.get('SCROLL_QUALIFICATION_OWNERSHIP'):
            registry = Path(env['SCROLL_QUALIFICATION_OWNERSHIP'])
            if registry.is_symlink():
                raise RuntimeError('ownership registry symlink rejected')
            registry = registry.resolve()
            registry.mkdir(parents=True, exist_ok=True, mode=0o700)
            if registry.is_symlink() or not registry.is_dir():
                raise RuntimeError('ownership registry boundary rejected')
        with raw_path.open('a') as output:
            child = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     env=env, start_new_session=True, shell=False)
            state.update(started=True, pid=child.pid, pgid=child.pid)
            publish('owned-created')
            if registry is not None:
                identity = process_identity(child.pid)
                if identity is None or identity['pgid'] != child.pid:
                    raise RuntimeError('created child identity unavailable')
                state['identity'] = identity
                durable(registry / (str(child.pid) + '-' + uuid.uuid4().hex + '.json'),
                        {'ownerPid': os.getpid(), 'pid': child.pid, 'pgid': child.pid, 'identity': identity}, exclusive=True)
            publish('owned-before-wait')
            until = time.monotonic() + deadline
            while child.poll() is None:
                if requested:
                    raise Cancelled(requested[0])
                remaining = until - time.monotonic()
                if remaining <= 0:
                    state['deadlineExceeded'] = True; break
                try:
                    child.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    pass
            state['naturalExit'] = child.poll() is not None and not requested
    except BaseException as error:
        failure = {'type': type(error).__name__, 'message': str(error)[:2000]}
    finally:
        # Keep deferring repeated signals until all owned cleanup and final
        # receipts complete. Nested scopes forward TERM before parent escalation.
        if child is not None:
            try:
                members = inspect()
                descendants = registered_descendants(registry, child.pid)
                if child.poll() is None or members != [] or requested:
                    state['naturalExit'] = False
                    send(child.pid, signal.SIGTERM, state.get('identity'))
                    try:
                        child.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        pass
                    if child.poll() is None or inspect() != []:
                        send(child.pid, signal.SIGKILL, state.get('identity'))
                    child.wait(timeout=5)
                # A nested session survives a killed intermediate supervisor.
                # Only the inherited fresh registry's proven descendant chain
                # is eligible, with PID/PGID birth identity checked before signal.
                descendants = registered_descendants(registry, child.pid)
                for entry in reversed(descendants):
                    if group_members(entry['pgid']):
                        state['naturalExit'] = False
                        send(entry['pgid'], signal.SIGTERM, entry['identity'])
                until = time.monotonic() + 2
                while any(group_members(entry['pgid']) for entry in descendants) and time.monotonic() < until:
                    time.sleep(0.02)
                for entry in reversed(descendants):
                    if group_members(entry['pgid']):
                        send(entry['pgid'], signal.SIGKILL, entry['identity'])
                until = time.monotonic() + 2
                members = inspect()
                while (members or any(group_members(entry["pgid"]) for entry in descendants)) and time.monotonic() < until:
                    time.sleep(0.02); members = inspect()
                state['nestedGroups'] = [{'pgid': entry['pgid'], 'remaining': group_members(entry['pgid'])} for entry in descendants]
                nested_clean = all(not entry['remaining'] for entry in state['nestedGroups'])
            except BaseException as error:
                failure = {'type': type(error).__name__, 'message': str(error)[:2000]}
        if requested:
            state['naturalExit'] = False
            if failure is None:
                failure = {'type': 'Cancelled', 'message': 'cancelled by ' + signal.Signals(requested[0]).name}
        state.update(returncode=child.returncode if child else None, reaped=bool(child and child.returncode is not None),
                     groupClean=bool(child and members == [] and inspection_failure is None and nested_clean),
                     remainingGroupMembers=members, elapsedSeconds=time.monotonic() - started,
                     failure=failure, inspectionFailure=inspection_failure)
        try:
            publish('finished')
            durable(final_path, state, exclusive=True)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    if requested:
        raise Cancelled(requested[0])
    return state
