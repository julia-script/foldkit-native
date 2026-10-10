// Exercise the byte-preserving transport tee with a harmless child, not gpuix.
import { test, expect } from 'bun:test'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

test('privacy tee preserves child stdout, stderr, exit, and original options', async () => {
  const root = mkdtempSync(join(tmpdir(), 'qualification-tee-'))
  const directory = join(root, 'packages/foldkit-gpuix/test')
  mkdirSync(directory, { recursive: true })
  const fixture = join(directory, 'secret-stale-native-app.ts')
  const output = '{"nativePrivacy":[]}\n'
  writeFileSync(fixture, `console.log('{"nativePrivacy":[]}');console.error('child stderr');process.exit(7)`)
  const before = Bun.spawn
  const savedTarget = process.env.QUALIFICATION_TARGET
  const savedEvidence = process.env.QUALIFICATION_EVIDENCE
  process.env.QUALIFICATION_TARGET = root
  process.env.QUALIFICATION_EVIDENCE = root
  try {
    await import('./capture-privacy.mjs')
    const options = { stdin: 'pipe', stdout: 'pipe', stderr: 'pipe', env: { ...process.env } }
    const child = Bun.spawn([process.execPath, fixture], options)
    const stdout = new Response(child.stdout).text()
    const stderr = new Response(child.stderr).text()
    expect(await child.exited).toBe(7)
    expect(await stdout).toBe(output)
    expect(await stderr).toBe('child stderr\n')
    child.kill() // The original parent calls kill again in its finally block.
    expect(readFileSync(join(root, `privacy-child-${child.pid}.json`), 'utf8')).toBe(output)
    expect(options.stdin).toBe('pipe')
    expect(options.stdout).toBe('pipe')
    expect(options.stderr).toBe('pipe')
  } finally {
    Bun.spawn = before
    if (savedTarget === undefined) delete process.env.QUALIFICATION_TARGET
    else process.env.QUALIFICATION_TARGET = savedTarget
    if (savedEvidence === undefined) delete process.env.QUALIFICATION_EVIDENCE
    else process.env.QUALIFICATION_EVIDENCE = savedEvidence
    rmSync(root, { recursive: true, force: true })
  }
})

test('CLI tee adds to bunfig preload and preserves an unchanged parent fixture', async () => {
  const root = mkdtempSync(join(tmpdir(), 'qualification-preload-'))
  const directory = join(root, 'packages/foldkit-gpuix/test')
  mkdirSync(directory, { recursive: true })
  const fixture = join(directory, 'secret-stale-native-app.ts')
  writeFileSync(fixture, `console.log('{"nativePrivacy":[]}')`)
  writeFileSync(join(root, 'bunfig.toml'), '[test]\npreload = ["./configured-preload.ts"]\n')
  writeFileSync(join(root, 'configured-preload.ts'), 'process.env.CONFIGURED_PRELOAD = "present"\n')
  writeFileSync(join(root, 'original.test.ts'), `import { test, expect } from 'bun:test';
test('unchanged parent', async () => {
  expect(process.env.CONFIGURED_PRELOAD).toBe('present');
  const child = Bun.spawn([process.execPath, ${JSON.stringify(fixture)}], { stdin:'pipe', stdout:'pipe', stderr:'pipe', env:{...process.env} });
  const stdout = new Response(child.stdout).text();
  const stderr = new Response(child.stderr).text();
  expect(await child.exited).toBe(0);
  expect(await stdout).toBe('{"nativePrivacy":[]}\\n');
  expect(await stderr).toBe('');
})`)
  try {
    const child = Bun.spawn([process.execPath, 'test', '--preload', join(import.meta.dir, 'capture-privacy.mjs'), 'original.test.ts'], {
      cwd: root, stdin: 'ignore', stdout: 'pipe', stderr: 'pipe',
      env: { ...process.env, QUALIFICATION_TARGET: root, QUALIFICATION_EVIDENCE: root },
    })
    const stdout = new Response(child.stdout).text()
    const stderr = new Response(child.stderr).text()
    expect(await child.exited).toBe(0)
    expect((await stdout) + (await stderr)).toContain('1 pass')
    const files = readdirSync(root).filter(name => name.startsWith('privacy-child-'))
    expect(files).toHaveLength(1)
    expect(readFileSync(join(root, files[0]), 'utf8')).toBe('{"nativePrivacy":[]}\n')
  } finally { rmSync(root, { recursive: true, force: true }) }
})
