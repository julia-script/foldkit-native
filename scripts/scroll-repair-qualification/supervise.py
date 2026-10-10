"""Bounded owned process groups; raw exit and cleanup verdict remain separate."""
import os
import signal
import subprocess
import time


def group_members(pgid):
    rows = subprocess.check_output(['ps', '-axo', 'pid=,pgid=,stat='], text=True, timeout=5)
    return [int(pid) for pid, group, status in (row.split() for row in rows.splitlines())
            if int(group) == pgid and not status.startswith('Z')]


def supervise(command, raw_path, env, deadline=60, state=None):
    state = {} if state is None else state
    started = time.monotonic()
    state.update(started=False, returncode=None, reaped=False, groupClean=False, remainingGroupMembers=None,
                 naturalExit=False, deadlineExceeded=False, signals=[], command=command, envelopeSeconds=deadline)
    child, failure, inspection_failure = None, None, None
    members = None

    def inspect():
        nonlocal inspection_failure
        try:
            return group_members(child.pid)
        except BaseException as error:
            inspection_failure = {'type': type(error).__name__, 'message': str(error)[:2000]}
            return None

    def send(sig):
        try:
            os.killpg(child.pid, sig)
            state['signals'].append(signal.Signals(sig).name)
        except ProcessLookupError:
            pass  # Only the PID/PGID created above is ever signalled.
        except PermissionError:
            if inspect() != []:
                raise

    try:
        # A log can be seeded by the owned artifact directory, never overwritten.
        with open(raw_path, 'a') as output:
            child = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     env=env, start_new_session=True, shell=False)
            state.update(started=True, pid=child.pid, pgid=child.pid)
            try:
                child.wait(timeout=deadline)
                state['naturalExit'] = True
            except subprocess.TimeoutExpired:
                state['deadlineExceeded'] = True
            finally:
                # Inspect only after recording PID ownership. Ordinary exit with
                # an empty group uses no signals. Retained children are failure.
                members = inspect()
                if child.poll() is None or members != []:
                    state['naturalExit'] = False
                    send(signal.SIGTERM)
                    try:
                        child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                    until = time.monotonic() + 2
                    members = inspect()
                    while members and time.monotonic() < until:
                        time.sleep(0.02)
                        members = inspect()
                    if child.poll() is None or members != []:
                        send(signal.SIGKILL)
                    child.wait(timeout=5)
                    until = time.monotonic() + 2
                    members = inspect()
                    while members and time.monotonic() < until:
                        time.sleep(0.02)
                        members = inspect()
    except BaseException as error:
        failure = {'type': type(error).__name__, 'message': str(error)[:2000]}
    finally:
        state.update(returncode=child.returncode if child else None, reaped=bool(child and child.returncode is not None),
                     groupClean=bool(child and members == [] and inspection_failure is None), remainingGroupMembers=members,
                     elapsedSeconds=time.monotonic() - started, failure=failure, inspectionFailure=inspection_failure)
    return state
