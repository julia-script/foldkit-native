// Diagnostic only. Durations are inclusive and overlap; never sum parents
// and children. Promise observers return the ORIGINAL promise to callers.
export type Metric = { calls: number; completed: number; errors: number; inFlight: number; postClose: number; ms: number; maxMs: number }
export const createObserver = (onClose: () => void = () => {}) => {
  const metrics: Record<string, Metric> = Object.create(null)
  const faults: Array<string> = []
  let phase = 'launch'
  let cycle = 0
  let settling = false
  let closed = false
  let closeCalls = 0
  let closeCompleted = 0
  let mutations = 0
  let mutationBatches = 0
  let batchDecodeMs = 0
  let requested: unknown
  let actual: unknown
  const restorers: Array<() => void> = []
  const safely = (work: () => void) => {
    try { work() } catch (error) { faults.push(String(error)) }
  }
  const key = (name: string) => `${phase}/${settling ? `cycle${cycle}` : 'outside'}/${name}`
  const begin = (name: string) => {
    const metric = metrics[key(name)] ??= { calls: 0, completed: 0, errors: 0, inFlight: 0, postClose: 0, ms: 0, maxMs: 0 }
    metric.calls++
    metric.inFlight++
    if (closed) metric.postClose++
    const at = performance.now()
    return (failed: boolean) => {
      const elapsed = performance.now() - at
      metric.completed++
      metric.inFlight--
      metric.errors += Number(failed)
      metric.ms += elapsed
      metric.maxMs = Math.max(metric.maxMs, elapsed)
    }
  }
  const observe = <F extends (...args: any[]) => any>(name: string, original: F,
    hooks: { before?: (args: Parameters<F>) => void; after?: (result: Awaited<ReturnType<F>>) => void; done?: () => void } = {}): F => {
    return function(this: unknown, ...args: Parameters<F>) {
      safely(() => hooks.before?.(args))
      const end = begin(name)
      const finish = (failed: boolean, result?: Awaited<ReturnType<F>>) => safely(() => {
        end(failed)
        if (!failed) hooks.after?.(result!)
        hooks.done?.()
      })
      try {
        const result = Reflect.apply(original, this, args) as ReturnType<F>
        if ((result as unknown) instanceof Promise) {
          // Adds two observer reactions, without replacing the result or
          // changing the rejection seen by the caller. No async wrapper.
          Promise.prototype.then.call(result, (value: Awaited<ReturnType<F>>) => finish(false, value), () => finish(true))
        } else finish(false, result as Awaited<ReturnType<F>>)
        return result
      } catch (error) {
        finish(true)
        throw error
      }
    } as F
  }
  const wrap = (target: object, name: string, label = name, hooks: Parameters<typeof observe>[2] = {}) => {
    const original = Reflect.get(target, name)
    if (typeof original !== 'function') throw new Error(`profile method missing: ${label}`)
    const descriptor = Object.getOwnPropertyDescriptor(target, name)
    Object.defineProperty(target, name, { configurable: true, writable: true, value: observe(label, original, hooks) })
    restorers.push(() => descriptor === undefined ? Reflect.deleteProperty(target, name) : Object.defineProperty(target, name, descriptor))
  }
  const attach = (app: { host: object; settle: () => Promise<void>; close: () => void }) => {
    wrap(app.host, 'frame', 'host.frame', { before: () => { if (settling) cycle++ } })
    for (const name of ['flush', 'drawn', 'dispatch']) wrap(app.host, name, `host.${name}`)
    wrap(app, 'settle', 'settle', {
      before: () => {
        if (settling) faults.push('overlapping settle calls')
        settling = true
        cycle = 0
      },
      done: () => {
        if (cycle !== 4) faults.push(`settle completed with ${cycle} cycles`)
        settling = false
      },
    })
    wrap(app, 'close', 'close', {
      before: () => { closeCalls++; closed = true },
      after: () => { closeCompleted++; onClose() },
    })
  }
  const instrumentRenderer = (renderer: object) => {
    for (const name of ['flush', 'dispatchNativeEvents', 'dispatchEvent', 'captureScreenshot', 'getAutomationTree', 'getElementBounds']) wrap(renderer, name, `renderer.${name}`)
    wrap(renderer, 'getWindowSize', 'renderer.getWindowSize', { after: result => { actual = result } })
    wrap(renderer, 'applyBatch', 'renderer.applyBatch', { before: args => {
      const at = performance.now()
      const batch: unknown = typeof args[0] === 'string' ? JSON.parse(args[0]) : undefined
      batchDecodeMs += performance.now() - at
      if (!Array.isArray(batch)) faults.push('non-array native batch JSON')
      else { mutations += batch.length; mutationBatches += Number(batch.length > 0) }
    } })
    for (const [name, next] of [['nativeSimulateClick', 'resizing'], ['nativeSimulateMouseDown', 'press'], ['dispatchMouseMove', 'drag'], ['nativeSimulateMouseUp', 'release']] as const) {
      wrap(renderer, name, `renderer.${name}`, { before: () => { phase = next } })
    }
    // Native flushes inside simulate* bypass renderer.flush. Observe both
    // levels; native.flush is the actual paint count, renderer.flush overlaps.
    const native = Reflect.get(renderer, 'native')
    if (native === undefined) throw new Error('profile requires real native backing object')
    for (const name of ['flush', 'drainEvents', 'simulateMouseMove', 'getAutomationTree', 'getElementBounds', 'getWindowSize']) wrap(native, name, `native.${name}`)
  }
  const instrumentTimers = (target: { setTimeout: typeof setTimeout }) => {
    wrap(target, 'setTimeout', 'timer.schedule', { })
    const scheduled = target.setTimeout
    // Preserve handles, delay/extra arguments, callback receiver and thrown
    // errors. Only observe timers already scheduled by the unchanged code.
    target.setTimeout = function(this: unknown, callback: TimerHandler, ...tail: any[]) {
      if (typeof callback !== 'function') return Reflect.apply(scheduled, this, [callback, ...tail])
      const delay = tail[0]
      const started = performance.now()
      const timerPhase = phase
      const timerCycle = settling && delay === 0 ? cycle + 1 : 0
      const wrapped = function(this: unknown, ...values: unknown[]) {
        const previousPhase = phase
        const previousCycle = cycle
        if (timerCycle > 0) { phase = timerPhase; cycle = timerCycle }
        // Wait duration is distinct from callback CPU duration.
        const elapsed = performance.now() - started
        const metric = metrics[key(`timer.wait.${delay ?? 0}`)] ??= { calls: 0, completed: 0, errors: 0, inFlight: 0, postClose: 0, ms: 0, maxMs: 0 }
        metric.calls++; metric.completed++; metric.postClose += Number(closed)
        metric.ms += elapsed; metric.maxMs = Math.max(metric.maxMs, elapsed)
        try { return Reflect.apply(observe(`timer.callback.${delay ?? 0}`, callback as (...args: unknown[]) => unknown), this, values) }
        finally { phase = previousPhase; cycle = previousCycle }
      }
      return Reflect.apply(scheduled, this, [wrapped, ...tail])
    } as typeof setTimeout
  }
  return {
    observe, wrap, attach, instrumentRenderer, instrumentTimers,
    requested: (size: unknown) => { requested = size },
    snapshot: () => ({ metrics, faults, requested, actual, closeCalls, closeCompleted, settling, mutations, mutationBatches, batchDecodeMs }),
    restore: () => { for (const restore of restorers.reverse()) restore() },
  }
}
