// Synthetic only: no TestRenderer construction, native library or window.
import { expect, test } from 'bun:test'
import { createObserver } from './observer.ts'

test('observer preserves receiver, arguments, synchronous results and exact throws', () => {
  const observer = createObserver()
  const object = { value: 4, call(a: object, b: number) { expect(this).toBe(object); return [a, b, this.value] } }
  const token = {}
  observer.wrap(object, 'call')
  expect(object.call(token, 3)).toEqual([token, 3, 4])
  expect(observer.observe('identity', () => token)()).toBe(token)
  const error = new Error('original failure')
  const fail = observer.observe('failure', () => { throw error })
  try { fail(); throw new Error('did not throw') } catch (caught) { expect(caught).toBe(error) }
  expect(Object.values(observer.snapshot().metrics).map(m => m.errors)).toEqual([0, 0, 1])
  observer.restore()
})

test('original fulfilled and rejected promises retain identity and caller outcome', async () => {
  const observer = createObserver()
  const value = {}
  const promised = Promise.resolve(value)
  const ok = observer.observe('async.ok', () => promised)
  expect(ok()).toBe(promised)
  expect(await promised).toBe(value)
  const error = new Error('async original failure')
  const rejected = Promise.reject(error)
  const fail = observer.observe('async.failure', () => rejected)
  expect(fail()).toBe(rejected)
  try { await rejected; throw new Error('did not reject') } catch (caught) { expect(caught).toBe(error) }
  expect(observer.snapshot().metrics['launch/outside/async.failure']!.errors).toBe(1)
})

test('real helper shape preserves four cycles and timer handles, counts late work after close', async () => {
  const observer = createObserver()
  let frames = 0
  let paints = 0
  let batches = 0
  class TestGpuixRenderer {
    flush() { paints++ }
    drainEvents() { return [] }
    simulateMouseMove() {}
    getAutomationTree() { return {} }
    getElementBounds() { return {} }
    getWindowSize() { return { width: 1024, height: 653 } }
  }
  const native = new TestGpuixRenderer()
  const renderer = {
    native, flush: () => native.flush(), dispatchNativeEvents: () => native.drainEvents(),
    dispatchEvent() {}, captureScreenshot() {}, getAutomationTree: () => native.getAutomationTree(),
    getElementBounds: () => native.getElementBounds(), getWindowSize: () => native.getWindowSize(),
    applyBatch: (json: string) => { batches += JSON.parse(json).length }, nativeSimulateClick() {},
    nativeSimulateMouseDown() {}, dispatchMouseMove: () => native.simulateMouseMove(), nativeSimulateMouseUp() {},
  }
  observer.instrumentRenderer(renderer)
  const host = { frame: () => { frames++ }, flush() {}, drawn() {}, dispatch() {} }
  const app = { host, settle: async () => {
    for (let i = 0; i < 4; i++) {
      await new Promise(done => setTimeout(done, 0))
      host.frame(); host.flush(); renderer.flush(); host.drawn(); renderer.dispatchNativeEvents()
    }
  }, close() {} }
  observer.attach(app)
  observer.instrumentTimers(globalThis)
  try {
    renderer.getWindowSize()
    renderer.applyBatch('[1,2]')
    await app.settle()
    app.close()
    await app.settle()
    expect({ frames, paints, batches }).toEqual({ frames: 8, paints: 8, batches: 2 })
    const snapshot = observer.snapshot()
    expect(snapshot.faults).toEqual([])
    expect(snapshot.closeCompleted).toBe(1)
    for (let cycle = 1; cycle <= 4; cycle++) {
      expect(snapshot.metrics[`launch/cycle${cycle}/native.flush`]!.calls).toBe(2)
      expect(snapshot.metrics[`launch/cycle${cycle}/native.flush`]!.postClose).toBe(1)
      expect(snapshot.metrics[`launch/cycle${cycle}/timer.wait.0`]!.calls).toBe(2)
    }
    const args = [{}, 19]
    let timer: ReturnType<typeof setTimeout> | undefined
    await new Promise<void>(done => {
      timer = setTimeout(function(...received: unknown[]) { expect(received).toEqual(args); done() }, 0, ...args)
      expect(timer).toBeDefined()
    })
    const cancelled = setTimeout(() => { throw new Error('cancelled timer ran') }, 1000)
    clearTimeout(cancelled)
  } finally { observer.restore() }
})

test('observer detects partial failing cycles without swallowing failure', async () => {
  const observer = createObserver()
  const error = new Error('original native failure')
  const app = { host: { frame() {}, flush() {}, drawn() {}, dispatch() {} },
    settle: async () => { app.host.frame(); throw error }, close() {} }
  observer.attach(app)
  try { await app.settle(); throw new Error('did not fail') } catch (caught) { expect(caught).toBe(error) }
  expect(observer.snapshot().faults).toEqual(['settle completed with 1 cycles'])
})

test('overlapping async settle calls are rejected as incomplete observation', async () => {
  const observer = createObserver()
  let release!: () => void
  const held = new Promise<void>(done => { release = done })
  const app = { host: { frame() {}, flush() {}, drawn() {}, dispatch() {} }, settle: () => held, close() {} }
  observer.attach(app)
  const first = app.settle()
  const second = app.settle()
  expect(first).toBe(held)
  expect(second).toBe(held)
  release()
  await held
  expect(observer.snapshot().faults).toContain('overlapping settle calls')
})

test('timer observer preserves omitted delay, scheduler receiver, handles and callback behavior', () => {
  const observer = createObserver()
  let scheduled: unknown[] = []
  const handle = {}
  const target = { setTimeout: function(this: unknown, ...args: unknown[]) {
    expect(this).toBe(target); scheduled = args; return handle
  } as unknown as typeof setTimeout }
  observer.instrumentTimers(target)
  const receiver = {}
  const returned = {}
  const callback = function(this: unknown, value: unknown) { expect(this).toBe(receiver); expect(value).toBe(returned); return returned }
  expect(Object.is(target.setTimeout(callback), handle)).toBe(true)
  expect(scheduled).toHaveLength(1)
  expect(Reflect.apply(scheduled[0] as Function, receiver, [returned])).toBe(returned)
  const error = new Error('original callback failure')
  target.setTimeout(() => { throw error }, 12, returned)
  expect(scheduled.slice(1)).toEqual([12, returned])
  try { Reflect.apply(scheduled[0] as Function, receiver, []); throw new Error('did not throw') } catch (caught) { expect(caught).toBe(error) }
  observer.restore()
})
