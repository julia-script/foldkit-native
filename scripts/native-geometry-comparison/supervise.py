"""Reviewed owned-group supervisor; 60s envelope preserves original 30s failure."""
import os
import signal
import subprocess
import time

def group_members(pgid):
    rows = subprocess.check_output(["ps", "-axo", "pid=,pgid=,stat="], text=True)
    return [int(pid) for pid, group, state in (line.split() for line in rows.splitlines())
            if int(group) == pgid and not state.startswith("Z")]

def supervise(command, raw_path, env, deadline=60, state=None):
    started = time.monotonic()
    state = {} if state is None else state
    state.update(returncode=None, deadlineExceeded=False, reaped=False, groupClean=False,
                 started=False, signals=[], command=command, envelopeSeconds=deadline)
    with open(raw_path, "w") as output:
        child = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                 env=env, start_new_session=True)
        state.update(started=True, pid=child.pid, pgid=child.pid)
        exceeded = False
        signals = state["signals"]
        def send(sig):
            try:
                os.killpg(child.pid, sig)
                signals.append(signal.Signals(sig).name)
            except ProcessLookupError:
                pass  # Owned group already exited: the expected cleanup race.
            except PermissionError:
                # macOS can retain an orphaned zombie group under launchd:
                # signalling it yields EPERM rather than ESRCH. Only accept
                # that race after proving there are NO live owned members.
                if group_members(child.pid):
                    raise
                signals.append("group-has-no-live-members")
        try:
            try:
                child.wait(timeout=deadline)
            except subprocess.TimeoutExpired:
                exceeded = True
                state["deadlineExceeded"] = True
                send(signal.SIGTERM)
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    send(signal.SIGKILL)
                    child.wait(timeout=5)
        finally:
            # Includes grandchildren retained after the original child exits.
            try:
                send(signal.SIGKILL)
                child.wait(timeout=5)
            finally:
                # Preserve a known raw exit even if signalling/reaping fails.
                state.update(returncode=child.returncode, reaped=child.returncode is not None,
                             elapsedSeconds=time.monotonic() - started)
        cleanup_deadline = time.monotonic() + 2
        members = group_members(child.pid)
        while members and time.monotonic() < cleanup_deadline:
            time.sleep(0.02)
            members = group_members(child.pid)
    state.update({"returncode": child.returncode, "deadlineExceeded": exceeded,
            "reaped": child.poll() is not None, "groupClean": not members,
            "remainingGroupMembers": members, "signals": signals,
            "pid": child.pid, "pgid": child.pid, "envelopeSeconds": deadline,
            "elapsedSeconds": time.monotonic() - started, "command": command})
    return state
