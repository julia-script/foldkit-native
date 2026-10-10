// Pure fault controls; this file never loads GPUI or opens a window.
import { expect, test } from 'bun:test'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { captureEvidence, disposal, hit, wait, writeDiagnostic } from './witness.ts'

test('failed first capture retains measured readiness, original image and guard diagnostics', () => {
  const out = mkdtempSync(join(tmpdir(), 'readme-capture-'))
  try {
    const initial = { count: 0, text: ['Count: 0'], painted: ['Count: 0', '+1'], frames: 9, id: 4, bounds: { x: 24, y: 24, width: 40, height: 20 }, window: { width: 800, height: 600 }, tree: { id: 4 }, point: { x: 44, y: 34 }, children: 1, containerConnected: false, buttonConnected: true }
    writeDiagnostic(out, 'initial-readiness', { phase: 'mounted', ...initial })
    let captures = 0, reads = 0
    expect(() => captureEvidence(out, 'count-0', 'mounted', path => {
      captures++
      writeFileSync(path, 'original mocked capture bytes')
    }, () => {
      reads++
      return { width: 800, height: 600, pixel: () => [0, 0, 0, 255] }
    })).toThrow('empty/uniform native screenshot')
    expect(captures).toBe(1)
    expect(reads).toBe(1)
    const ready = JSON.parse(readFileSync(join(out, 'initial-readiness.json'), 'utf8'))
    expect(ready).toMatchObject(initial)
    expect(readFileSync(join(out, 'count-0.png'), 'utf8')).toBe('original mocked capture bytes')
    expect(JSON.parse(readFileSync(join(out, 'count-0-capture.json'), 'utf8'))).toMatchObject({ phase: 'mounted', status: 'failed', stage: 'nonuniform-guard', width: 800, height: 600, first: [0, 0, 0, 255], varied: 0, error: { message: 'empty/uniform native screenshot' } })
  } finally { rmSync(out, { recursive: true, force: true }) }
})

test('healthy capture retains its original return value and diagnostics without another native call', () => {
  const out = mkdtempSync(join(tmpdir(), 'readme-capture-'))
  try {
    let captures = 0
    const result = captureEvidence(out, 'count-1', 'native-input', path => { captures++; writeFileSync(path, 'mock healthy bytes') }, () => ({ width: 12, height: 12, pixel: (x, y) => [x, y, 0, 255] }))
    expect(result).toEqual({ path: join(out, 'count-1.png'), width: 12, height: 12, varied: 143 })
    expect(captures).toBe(1)
    expect(JSON.parse(readFileSync(join(out, 'count-1-capture.json'), 'utf8'))).toMatchObject({ status: 'passed', width: 12, height: 12, varied: 143 })
  } finally { rmSync(out, { recursive: true, force: true }) }
})

test('capture and decoder errors preserve original identity even if diagnostic writing fails', () => {
  const out = mkdtempSync(join(tmpdir(), 'readme-capture-'))
  try {
    const original = new Error('original mocked capture failure')
    for (const failAt of ['capture', 'decode', 'diagnostic-write']) {
      if (failAt === 'diagnostic-write') mkdirSync(join(out, `${failAt}-capture.json`))
      let caught: unknown
      try { captureEvidence(out, failAt, 'mounted', () => { if (failAt !== 'decode') throw original }, () => { throw original }) } catch (error) { caught = error }
      expect(caught).toBe(original)
      if (failAt !== 'diagnostic-write') expect(JSON.parse(readFileSync(join(out, `${failAt}-capture.json`), 'utf8'))).toMatchObject({ status: 'failed', stage: failAt, error: { message: original.message } })
    }
  } finally { rmSync(out, { recursive: true, force: true }) }
})

test('capture diagnostics preserve healthy zero and failed nonzero original child exits', () => {
  const out = mkdtempSync(join(tmpdir(), 'readme-capture-exit-'))
  try {
    for (const healthy of [false, true]) {
      const name = healthy ? 'healthy' : 'uniform'
      const script = `import { captureEvidence } from ${JSON.stringify(import.meta.dir + '/witness.ts')}; import { writeFileSync } from 'node:fs'; process.on('exit', code => writeFileSync(${JSON.stringify(join(out, 'exit.json'))}, JSON.stringify({code}))); captureEvidence(${JSON.stringify(out)}, ${JSON.stringify(name)}, 'mounted', path => writeFileSync(path, 'mock capture bytes'), () => ({width: 12, height: 12, pixel: ${healthy ? '(x, y) => [x, y, 0, 255]' : '() => [0, 0, 0, 255]'}}));`
      const child = Bun.spawnSync([process.execPath, '--eval', script], { stdout: 'pipe', stderr: 'pipe', timeout: 5000 })
      expect(child.exitCode).toBe(healthy ? 0 : 1)
      expect(JSON.parse(readFileSync(join(out, 'exit.json'), 'utf8'))).toEqual({ code: healthy ? 0 : 1 })
      expect(JSON.parse(readFileSync(join(out, `${name}-capture.json`), 'utf8'))).toMatchObject({ status: healthy ? 'passed' : 'failed', varied: healthy ? 143 : 0 })
      if (!healthy) expect(new TextDecoder().decode(child.stderr)).toContain('empty/uniform native screenshot')
    }
  } finally { rmSync(out, { recursive: true, force: true }) }
})

test('native hit uses actual visible content and rejects empty/invisible bounds', () => {
  expect(hit({ x: 414, y: 634, width: 134, height: 40 }, { width: 760, height: 653 })).toEqual({ x: 481, y: 643.5 })
  for (const box of [null, { x: 414, y: 654, width: 134, height: 40 }, { x: 0, y: 0, width: 0, height: 1 }, { x: NaN, y: 0, width: 1, height: 1 }]) {
    expect(() => hit(box, { width: 760, height: 653 })).toThrow()
  }
})

test('bounded readiness rejects absent/stale state and waits for genuine advancement', async () => {
  let clock = 0, reads = 0
  const pause = async () => { clock += 50 }
  expect(await wait(() => ++reads === 3 ? { count: 1 } : undefined, () => clock, pause, 200)).toEqual({ count: 1 })
  expect(reads).toBe(3)
  await expect(wait(() => undefined, () => clock, pause, 200)).rejects.toThrow('deadline')
  expect(clock).toBe(300)
})

test('ownership witness rejects missing runtime disposal even when native host tears down', () => {
  expect(() => disposal(true, true, true, true, true, true)).not.toThrow()
  expect(() => disposal(true, false, false, true, true, true)).toThrow('runtime')
  expect(() => disposal(false, true, true, true, true, true)).toThrow('mounted')
  for (const flags of [[false, true, true], [true, false, true], [true, true, false]]) {
    expect(() => disposal(true, true, true, flags[0]!, flags[1]!, flags[2]!)).toThrow('resources')
  }
})
