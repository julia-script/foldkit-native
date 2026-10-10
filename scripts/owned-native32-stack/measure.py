"""One frozen original native test and one exact-owned-PID external sample.

No observer, native wrapper, timing/fixture change, retry or local native mode.
Sample trees contain inclusive occupancy, not exclusive CPU or paint counts.
"""
import sys
sys.dont_write_bytecode = True
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

BASE = '7b5aaf49eb9ee49b5c6a596a8e6db8d1207f25fa'
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TITLE = 'a drag across a 32 × 32 board through GPUI: every cell it crosses, and how many moves a second'
FILTER = '^Metal, offscreen.*' + re.escape(TITLE) + '$'
ORIGINAL = ['bun', 'test', './examples/pixel-art/native.test.ts', '--test-name-pattern', FILTER]
NATIVE_SHA = '76018707ee53f38044ea5a77a56b5c80ff4b755150a826aff07a8992431c9213'
PINS = {'foldkit': '0.165.0', '@foldkit/ui': '0.165.0', 'effect': '4.0.0',
        '@gpuix/native': '0.10.0', 'happy-dom': '20.14.5', '@gpuix/native-darwin-arm64': '0.10.0'}
LIMITS = {'originalEnvelopeSeconds': 60, 'sampleSeconds': 25, 'sampleIntervalMilliseconds': 10,
          'samplerEnvelopeSeconds': 35, 'marker': 'native test window:',
          'phaseUncertainty': 'Window log precedes attach; no immutable drag-start marker. Startup, resize and drag may overlap.',
          'overhead': 'sample suspends/resumes the target; its overhead can perturb the unchanged 30-second deadline.',
          'interpretation': 'Inclusive sampled occupancy only; not exclusive CPU, paint count, speedup or causal attribution.'}


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stamp():
    return {'utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'monotonic': time.monotonic()}


def save(out, name, value):
    # Each immutable receipt has one writer; collisions cannot replace history.
    with (out / (name + '.json')).open('x') as file:
        json.dump(value, file, indent=2)


def read_command(argv, cwd=ROOT):
    return subprocess.check_output(argv, cwd=cwd, text=True, timeout=15)


def source_identity(root=ROOT):
    head = read_command(['git', 'rev-parse', 'HEAD'], root).strip()
    require(head == os.environ.get('GITHUB_SHA'), 'wrong reviewed driver head')
    require(not read_command(['git', 'status', '--porcelain', '--untracked-files=all'], root), 'dirty qualification checkout')
    names = read_command(['git', 'ls-tree', '-r', '--name-only', BASE], root).splitlines()
    frozen = {}
    for name in names:
        expected = subprocess.check_output(['git', 'show', BASE + ':' + name], cwd=root, timeout=15)
        require((root / name).read_bytes() == expected, 'frozen main bytes changed: ' + name)
        frozen[name] = sha(expected)
    changed = read_command(['git', 'diff', '--name-only', BASE, head], root).splitlines()
    allowed = {'scripts/owned-native32-stack/' + name for name in ('measure.py', 'controls_test.py', 'prepare.py')}
    allowed.add('.github/workflows/owned-native32-stack-measurement.yml')
    require(changed and set(changed) <= allowed, 'non-qualification delta')
    source = (root / 'examples/pixel-art/native.test.ts').read_text()
    require(source.count("test('" + TITLE + "'") == 1 and 'const SLOW = 30_000' in source, 'original anchored fixture contract missing')
    helper = (root / 'packages/foldkit-gpuix/test/support.ts').read_text()
    require("console.log('native test window:'" in helper and 'for (let i = 0; i < 4; i++)' in helper, 'immutable marker/four cycles absent')
    return {'head': head, 'base': BASE, 'mainHashes': frozen,
            'driverHashes': {name: sha((root / name).read_bytes()) for name in sorted(allowed)}}


def dependency_identity(root=ROOT):
    packages = {}
    for name, version in PINS.items():
        raw = (root / 'node_modules' / name / 'package.json').read_bytes()
        info = json.loads(raw)
        require(info['name'] == name and info['version'] == version, 'package identity/pin mismatch')
        packages[name] = {'name': name, 'version': version, 'packageSHA256': sha(raw)}
    binary = root / 'node_modules/@gpuix/native-darwin-arm64/gpuix-native.darwin-arm64.node'
    require(sha(binary.read_bytes()) == NATIVE_SHA, 'installed native binary bytes differ')
    load = read_command(['/usr/bin/otool', '-l', str(binary)], root)
    uuids = re.findall(r'\buuid ([0-9A-Fa-f-]{36})', load)
    require(len(uuids) == 1, 'ambiguous/missing installed Mach-O UUID')
    return {'packages': packages, 'binary': {'path': str(binary.resolve()), 'sha256': sha(binary.read_bytes()),
            'uuid': uuids[0].upper(), 'otoolSHA256': sha(load.encode())}}


def table():
    # Read-only inventory; never signal a process by name or an arbitrary PID.
    raw = read_command(['/bin/ps', '-axo', 'pid=,ppid=,pgid=,lstart=,rss=,time=,stat=,comm='])
    result = {}
    for row in raw.splitlines():
        cols = row.split(None, 11)
        if len(cols) == 12:
            pid, ppid, pgid = map(int, cols[:3])
            result[pid] = {'pid': pid, 'ppid': ppid, 'pgid': pgid, 'start': ' '.join(cols[3:8]),
                           'rssKB': cols[8], 'cpuTime': cols[9], 'state': cols[10], 'command': cols[11]}
    return result


def token(row):
    return {key: row[key] for key in ('pid', 'pgid', 'start', 'command')}


class Owned:
    def __init__(self, argv, out, role, env, factory=subprocess.Popen):
        self.role, self.known, self.child = role, {}, None
        self.receipt = {'role': role, 'argv': argv, 'start': stamp(), 'rawExit': None,
                        'signals': [], 'cleanupVerified': False, 'survivors': [], 'identity': None}
        self.stdout = (out / (role + '.stdout.log')).open('x')
        self.stderr = (out / (role + '.stderr.log')).open('x')
        try:
            self.child = factory(argv, cwd=ROOT, env=env, stdout=self.stdout, stderr=self.stderr, start_new_session=True)
            self.observe()
        except BaseException as error:
            self.receipt['spawnOrIdentityError'] = repr(error)
            self.close()
            save(out, role + '-process', self.receipt)
            raise

    def observe(self):
        rows = table()
        if self.child.poll() is None:
            row = rows.get(self.child.pid)
            require(row and row['ppid'] == os.getpid() and row['pgid'] == self.child.pid, 'wrong owned child PID/group/parent')
            if self.receipt['identity']:
                require(token(row) == token(self.receipt['identity']), 'stale owned child identity')
            else:
                self.receipt['identity'] = row
            # Same newly-created session/group while its known leader is live.
            for pid, member in rows.items():
                if member['pgid'] == self.child.pid:
                    self.known[pid] = token(member)
            self.receipt['lastLive'] = stamp()
            self.receipt['lastResources'] = row
        return rows

    def verified_live(self):
        require(self.child is not None and self.child.poll() is None, 'owned target exited before sample')
        rows = self.observe()
        require(self.receipt['identity'] and token(rows[self.child.pid]) == token(self.receipt['identity']), 'stale owned target before sample')
        return self.receipt['identity']

    def send(self, sig):
        rows = self.observe()
        members = [row for row in rows.values() if row['pgid'] == self.child.pid and not row['state'].startswith('Z')]
        require(all(self.known.get(row['pid']) == token(row) for row in members), 'unverified group member: cleanup refused')
        if members:
            try:
                os.killpg(self.child.pid, sig)
                self.receipt['signals'].append(signal.Signals(sig).name)
            except ProcessLookupError:
                pass
            except PermissionError:
                require(not [r for r in table().values() if r['pgid'] == self.child.pid and not r['state'].startswith('Z')], 'owned signal denied with live members')

    def close(self):
        try:
            if self.child:
                if self.child.poll() is None:
                    self.send(signal.SIGTERM)
                    try:
                        self.child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.send(signal.SIGKILL)
                        self.child.wait(timeout=5)
                self.send(signal.SIGKILL)
                self.receipt['rawExit'] = self.child.returncode
                end = time.monotonic() + 2
                survivors = [r for r in table().values() if r['pgid'] == self.child.pid and not r['state'].startswith('Z')]
                while survivors and time.monotonic() < end:
                    time.sleep(.02)
                    survivors = [r for r in table().values() if r['pgid'] == self.child.pid and not r['state'].startswith('Z')]
                self.receipt['orphanZombiesNotReapedBySupervisor'] = [r for r in table().values() if r['pgid'] == self.child.pid and r['state'].startswith('Z')]
                self.receipt['survivors'] = survivors
                self.receipt['cleanupVerified'] = self.child.returncode is not None and not survivors
        except BaseException as error:
            self.receipt['cleanupError'] = repr(error)
            if self.child:
                self.receipt['rawExit'] = self.child.returncode
        finally:
            self.receipt['end'] = stamp()
            self.stdout.close()
            self.stderr.close()
        return self.receipt


def sample_command(target, out):
    identity = target.verified_live()
    require(identity['pid'] == target.child.pid and identity['pgid'] == target.child.pid, 'sample PID not owned target')
    require(Path(identity['command']).name == 'bun', 'sample target is not the original Bun executable')
    require(not (out / 'sample.txt').exists(), 'sample output collision')
    return ['/usr/bin/sample', str(target.child.pid), '25', '10', '-mayDie', '-file', str(out / 'sample.txt')]


def marker(raw):
    matches = re.findall(r'^native test window: (\{[^\n]*\})\s*$', raw, re.M)
    if not matches:
        return None
    require(len(matches) == 1, 'multiple native window markers')
    packet = json.loads(matches[0])
    require(packet['name'] == 'pixel-art' and packet['actual']['width'] > 0 and packet['actual']['height'] > 0, 'wrong native window marker')
    return packet


def validate_report(raw, original, sampler, binary, synthetic=False):
    require(not synthetic, 'synthetic report cannot establish physical stack evidence')
    require(sampler['rawExit'] == 0 and sampler['cleanupVerified'], 'sample command failed or cleanup unverified')
    pid = original['identity']['pid']
    require(sampler['targetIdentity'] == token(original['identity']), 'stale target identity recorded by sampler')
    require(sampler['argv'][:6] == ['/usr/bin/sample', str(pid), '25', '10', '-mayDie', '-file'] and len(sampler['argv']) == 7, 'sampler argv/PID drift')
    require(re.search(r'^Process:\s+bun \[' + str(pid) + r'\]\s*$', raw, re.M), 'report PID/name not original owned Bun')
    require('Call graph:' in raw and 'Binary Images:' in raw, 'missing stack/image sections')
    require(re.search(r'^\s*[+|:! ]*\d+\s+\S+', raw.split('Call graph:', 1)[1].split('Binary Images:', 1)[0], re.M), 'empty/malformed stack tree')
    image = [line for line in raw.split('Binary Images:', 1)[1].splitlines() if line.rstrip().endswith(binary['path'])]
    require(len(image) == 1 and '<' + binary['uuid'] + '>' in image[0].upper(), 'loaded-image UUID/path differs from hashed installed binary')
    require(binary['sha256'] == NATIVE_SHA, 'report linkage has wrong installed binary hash')
    date = re.search(r'^Date/Time:\s+(.+)$', raw, re.M)
    require(date is not None, 'sample date missing')
    when = dt.datetime.strptime(date[1].strip(), '%Y-%m-%d %H:%M:%S.%f %z').timestamp()
    first = dt.datetime.fromisoformat(sampler['start']['utc']).timestamp()
    last = dt.datetime.fromisoformat(sampler['end']['utc']).timestamp()
    require(first - 1 <= when <= last + 1, 'stale report timestamp outside owned sampling interval')
    image_name = re.match(r'\s*0x[0-9a-f]+\s*-\s*0x[0-9a-f]+\s+\+?(\S+)', image[0], re.I)
    require(image_name is not None, 'malformed loaded-image address/name')
    frames = [line for line in raw.split('Call graph:', 1)[1].split('Binary Images:', 1)[0].splitlines()
              if '(in ' + image_name[1] + ')' in line]
    return {'reportPID': pid, 'loadedImage': image[0], 'installedBinary': binary,
            'nativeImageFrameLines': frames, 'symbolAttributionAvailable': bool(frames),
            'samplingInterval': [sampler['start'], sampler['end']], 'phaseUncertainty': LIMITS['phaseUncertainty'],
            'interpretation': LIMITS['interpretation'], 'attribution': 'Only this matching binary image; no source-tag build attestation.'}


def test_verdict(raw, receipt):
    clean = re.sub(r'\x1b\[[0-9;]*m', '', raw)
    rows = re.findall(r'^\((pass|fail|skip)\) (.+)$', clean, re.M)
    executed = [(kind, name) for kind, name in rows if kind != 'skip']
    require(len(executed) == 1 and TITLE in executed[0][1] and 'Metal, offscreen' in executed[0][1], 'original32 did not execute exactly once, or other tests executed')
    if receipt['rawExit'] == 0:
        require(executed[0][0] == 'pass' and re.search(r'\b33 expect\(\) calls', clean), 'passing original lacks unchanged 33 assertions')
        return 'pass'
    return 'fail'


def run(out):
    require(not out.is_relative_to(ROOT), 'evidence must be outside frozen checkout')
    out.mkdir(parents=True, exist_ok=False)
    state = {'completeEvidence': False, 'originalVerdict': 'unproved', 'originalRawExit': None,
             'samplerRawExit': None, 'synthetic': False, 'limits': LIMITS, 'start': stamp()}
    original = sampler = None
    source = deps = None
    try:
        require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.uname().sysname == 'Darwin'
                and os.environ.get('GITHUB_REPOSITORY') == 'julia-script/foldkit-native'
                and os.environ.get('GITHUB_REF') == 'refs/heads/julia/owned-native32-stack-measurement'
                and os.environ.get('GITHUB_EVENT_NAME') == 'push'
                and os.environ.get('GITHUB_RUN_ATTEMPT') == '1', 'hosted macOS release only; local native execution forbidden')
        require(not any(k in os.environ for k in ('FOLDKIT_NATIVE_NO_WINDOW', 'NAPI_RS_NATIVE_LIBRARY_PATH', 'BUN_PRELOAD', 'NODE_OPTIONS')), 'alternate/preloaded/headless runtime forbidden')
        source = source_identity()
        save(out, 'source-before', source)
        require(read_command(['bun', '--version']).strip() == '1.3.9' and read_command(['node', '--version']).strip() == 'v22.22.3', 'runtime pins differ')
        deps = dependency_identity()
        save(out, 'dependencies-before', deps)
        symbols = read_command(['/usr/bin/nm', deps['binary']['path']])
        (out / 'native-symbols.log').write_text(symbols)
        save(out, 'symbols', {'binary': deps['binary'], 'nmSHA256': sha(symbols.encode()),
             'interpretation': 'Local installed symbols; not upstream tag/build attestation or observed calls.'})
        save(out, 'planned-command', {'original': ORIGINAL, 'samplerTemplate': ['/usr/bin/sample', 'OWNED_PID', '25', '10', '-mayDie', '-file', 'EXCLUSIVE_OUTPUT'], 'limits': LIMITS})
        (out / 'native').mkdir()
        env = {**os.environ, 'FOLDKIT_NATIVE_RENDERER': 'gpuix', 'FOLDKIT_NATIVE_EVIDENCE': str(out / 'native'), 'PYTHONDONTWRITEBYTECODE': '1'}
        original = Owned(ORIGINAL, out, 'original', env)
        start = time.monotonic()
        last_observe = start
        while original.child.poll() is None:
            now = time.monotonic()
            if now - start > LIMITS['originalEnvelopeSeconds']:
                state['originalEnvelopeExceeded'] = True
                raise TimeoutError('original envelope exceeded; unchanged test raw failure preserved')
            if now - last_observe >= 1:
                original.observe()
                if sampler:
                    sampler.observe()
                last_observe = now
            if sampler is None:
                found = marker((out / 'original.stdout.log').read_text(errors='replace') + (out / 'original.stderr.log').read_text(errors='replace'))
                if found:
                    save(out, 'marker-observed', {'observed': stamp(), 'marker': found, 'limits': LIMITS})
                    argv = sample_command(original, out)
                    save(out, 'target-at-sample-start', original.verified_live())
                    sampler = Owned(argv, out, 'sampler', env)
                    sampler.receipt['targetIdentity'] = token(original.receipt['identity'])
            if sampler and sampler.child.poll() is None and now - sampler.receipt['start']['monotonic'] > LIMITS['samplerEnvelopeSeconds']:
                state['samplerEnvelopeExceeded'] = True
                raise TimeoutError('sampler envelope exceeded')
            time.sleep(.05)
        require(sampler is not None, 'original exited before immutable native-window marker; sampler not launched')
        remaining = max(0, LIMITS['samplerEnvelopeSeconds'] - (time.monotonic() - sampler.receipt['start']['monotonic']))
        sampler.child.wait(timeout=remaining)
    except BaseException as error:
        state['error'] = repr(error)
    finally:
        for child in (sampler, original):
            if child:
                receipt = child.close()
                save(out, child.role + '-process', receipt)
        for role, child in (('original', original), ('sampler', sampler)):
            failed = out / (role + '-process.json')
            receipt = child.receipt if child else json.loads(failed.read_text()) if failed.exists() else {}
            state[role + 'RawExit'] = receipt.get('rawExit')
        try:
            if source:
                after = source_identity()
                save(out, 'source-after', after)
                require(after == source, 'source/driver identity changed')
            if deps:
                after = dependency_identity()
                save(out, 'dependencies-after', after)
                require(after == deps, 'package/native identity changed')
            require(original and original.receipt['cleanupVerified'], 'original cleanup not proven')
            raw = (out / 'original.stdout.log').read_text(errors='replace') + (out / 'original.stderr.log').read_text(errors='replace')
            state['originalVerdict'] = test_verdict(raw, original.receipt)
            require(sampler is not None, 'sample unavailable')
            report = validate_report((out / 'sample.txt').read_text(), original.receipt, sampler.receipt, deps['binary'])
            save(out, 'stack-evidence', report)
            if state['originalVerdict'] == 'pass':
                png = out / 'native/pixel-art-big-stroke.png'
                require(png.is_file() and png.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'), 'original passing screenshot absent')
            require(not state.get('error'), 'earlier execution failure remains')
            state['completeEvidence'] = True
        except BaseException as error:
            state['evidenceError'] = repr(error)
        state['end'] = stamp()
        save(out, 'result', state)
        save(out, 'manifest', {str(p.relative_to(out)): sha(p.read_bytes()) for p in out.rglob('*') if p.is_file()})
    raw_exit = state['originalRawExit']
    return (raw_exit if raw_exit > 0 else 128 - raw_exit) if isinstance(raw_exit, int) and raw_exit != 0 else (0 if state['completeEvidence'] else 1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    def interrupted(signum, _frame):
        raise InterruptedError('owned supervisor signal ' + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(run(args.out.resolve()))
