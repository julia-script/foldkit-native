// Pure fault controls; this file never loads GPUI or opens a window.
import { expect, test } from 'bun:test'
import { disposal, hit, wait } from './witness.ts'

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
