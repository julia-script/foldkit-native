// Observations around the verbatim README snippet; no runtime or input replacement.
import assert from 'node:assert/strict'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { join } from 'node:path'
import type { mountGpuix, NativeElement } from 'foldkit-gpuix'
import { context, measure, pixels, sameScene, styles } from './contrast.ts'
import { readCapturePng } from './png.ts'

type Box = { x: number; y: number; width: number; height: number }
type Size = { width: number; height: number }
type App = ReturnType<typeof mountGpuix>
type NativeObservations = {
  isInitialized(): boolean
  getAllText(): Array<string>
  getPaintedText(): Array<string>
  getAutomationTree(): string
  getElementBounds(id: number): Box | null
  getWindowSize(): Size
  getDebugFrameOverlayStats(): { frames: number }
  captureScreenshot(path: string): void
  simulateClick(x: number, y: number): void
}

export function hit(bounds: Box | null, window: Size) {
  assert(bounds !== null, 'missing native bounds')
  assert(Object.values(bounds).every(Number.isFinite) && Object.values(window).every(Number.isFinite))
  assert(bounds.width > 0 && bounds.height > 0 && window.width > 0 && window.height > 0)
  const left = Math.max(0, bounds.x), top = Math.max(0, bounds.y)
  const right = Math.min(window.width, bounds.x + bounds.width), bottom = Math.min(window.height, bounds.y + bounds.height)
  assert(right > left && bottom > top, 'native target is outside the paintable window')
  return { x: (left + right) / 2, y: (top + bottom) / 2 }
}

export async function wait<T>(read: () => T | undefined, now = () => performance.now(), pause = () => Bun.sleep(50), timeout = 10_000): Promise<T> {
  const deadline = now() + timeout
  while (now() < deadline) {
    const value = read()
    if (value !== undefined) return value
    await pause()
  }
  throw new Error('native readiness/post-input deadline expired')
}

export function disposal(mounted: boolean, containerRestored: boolean, buttonDisconnected: boolean, hostDetached: boolean, restored: boolean, stateDetached: boolean) {
  assert(mounted, 'runtime was never mounted')
  assert(containerRestored && buttonDisconnected, 'owned runtime did not restore its original container')
  assert(hostDetached && restored && stateDetached, 'adapter resources were not released')
}

export function writeDiagnostic(out: string, name: string, values: Record<string, unknown>) {
  writeFileSync(join(out, `${name}.json`), JSON.stringify({ pid: process.pid, at: Date.now(), ...values }, null, 2))
}

export function captureEvidence(out: string, name: string, phase: string, captureScreenshot: (path: string) => void, readPng: (path: string) => { width: number; height: number; pixel(x: number, y: number): Array<number> }) {
  const path = join(out, `${name}.png`)
  const diagnostic: Record<string, unknown> = { name, path, phase, stage: 'capture', status: 'started' }
  try {
    captureScreenshot(path)
    diagnostic.stage = 'decode'
    const image = readPng(path)
    diagnostic.width = image.width
    diagnostic.height = image.height
    diagnostic.stage = 'dimensions-guard'
    assert(image.width > 0 && image.height > 0)
    diagnostic.stage = 'pixel-scan'
    const first = image.pixel(0, 0)
    diagnostic.first = first
    let varied = 0
    for (let y = 0; y < image.height; y++) for (let x = 0; x < image.width; x++) {
      if (image.pixel(x, y).some((v: number, i: number) => v !== first[i])) varied++
    }
    diagnostic.varied = varied
    diagnostic.stage = 'nonuniform-guard'
    assert(varied > 100, 'empty/uniform native screenshot')
    diagnostic.status = 'passed'
    writeDiagnostic(out, `${name}-capture`, diagnostic)
    return { path, width: image.width, height: image.height, varied }
  } catch (error) {
    diagnostic.status = 'failed'
    diagnostic.error = error instanceof Error ? { name: error.name, message: error.message, stack: error.stack } : { message: String(error) }
    // Artifact writing must not replace the original capture/decode/guard error.
    try { writeDiagnostic(out, `${name}-capture`, diagnostic) } catch {}
    throw error
  }
}

export function begin() {
  const out = process.env['QUALIFICATION_EVIDENCE']!
  const root = process.env['QUALIFICATION_TARGET']!
  assert(out && root)
  mkdirSync(out, { recursive: true })
  const originalDocument = globalThis.document, originalWindow = globalThis.window
  const rows: Array<Record<string, unknown>> = []
  let app: App | undefined
  let renderedButton: NativeElement | undefined
  let beforeClose = 0, closeRequests = 0, phase = 'before-snippet'
  let state: ((renderer: App['renderer']) => { current(): unknown }) | undefined
  const record = (kind: string, values: Record<string, unknown>) => {
    rows.push({ kind, phase, pid: process.pid, at: Date.now(), ...values })
    writeFileSync(join(out, 'observations.json'), JSON.stringify(rows, null, 2))
    console.log(JSON.stringify(rows.at(-1)))
  }
  record('startup', { platform: process.platform, arch: process.arch, bun: Bun.version, native: true })
  process.on('exit', code => {
    const exit = {
      code, closeRequests, beforeClose,
      children: app?.container.childNodes.length,
      containerRestored: app !== undefined && app.container.isConnected,
      buttonDisconnected: renderedButton !== undefined && !renderedButton.isConnected,
      hostDetached: app !== undefined && app.document.host === undefined,
      globalsRestored: globalThis.document === originalDocument && globalThis.window === originalWindow,
      stateDetached: app !== undefined && state !== undefined && state(app.renderer).current() === undefined,
    }
    // Do not alter the app's original exit code; the external validator decides acceptance.
    let disposalError: string | null = null
    try {
      disposal(beforeClose > 0, exit.containerRestored, exit.buttonDisconnected, exit.hostDetached, exit.globalsRestored, exit.stateDetached)
    } catch (error) {
      disposalError = String(error)
    }
    record('exit', { ...exit, disposalError })
  })
  return async (mounted: App) => {
    app = mounted
    phase = 'mounted'
    const host = await import(Bun.resolveSync('@gpuix/native/host', root))
    state = host.createRendererState
    const { readPng } = await import(join(root, 'test/support/png.ts'))
    // mountGpuix owns a production GpuixRenderer; its public WindowRenderer
    // type exposes fewer methods than the pinned binary's observation API.
    const renderer = app.renderer as App['renderer'] & NativeObservations
    for (const method of ['isInitialized', 'getAllText', 'getPaintedText', 'getAutomationTree', 'getElementBounds', 'getWindowSize', 'getDebugFrameOverlayStats', 'captureScreenshot', 'simulateClick'] as const) {
      assert.equal(typeof renderer[method], 'function', `native method missing: ${method}`)
    }
    assert(renderer.isInitialized(), 'native renderer did not initialize')
    assert.equal(process.env['FOLDKIT_NATIVE_NO_WINDOW'], undefined)
    const snapshot = (count: number) => {
      const text = renderer.getAllText(), painted = renderer.getPaintedText()
      const label = `Count: ${count}`
      if (!text.includes(label) || !painted.includes(label) || !painted.includes('+1')) return undefined
      const buttons = app!.document.querySelectorAll('button')
      if (buttons.length !== 1 || buttons[0]!.textContent !== '+1') return undefined
      const id = buttons[0]!.nativeId
      const bounds = renderer.getElementBounds(id), window = renderer.getWindowSize()
      if (id <= 0 || bounds === null) return undefined
      const tree = JSON.parse(renderer.getAutomationTree())
      const contains = (node: { id: number; children?: Array<typeof node> }): boolean => node.id === id || (node.children ?? []).some(contains)
      if (tree === null || !contains(tree)) return undefined
      const point = hit(bounds, window)
      return { count, text, painted, tree, id, bounds, window, point, children: app!.document.body.childNodes.length, containerConnected: app!.container.isConnected, buttonConnected: buttons[0]!.isConnected, frames: renderer.getDebugFrameOverlayStats().frames }
    }
    const control = process.env['QUALIFICATION_CASE'] === 'control' || process.env['QUALIFICATION_CASE'] === 'corrected'
    const capture = (name: string) => {
      // The original capture/guard/error path stays exactly the same.
      const count = name === 'count-0' ? 0 : 1
      const before = control ? context(app!, renderer, count, measure(process.env['QUALIFICATION_FRAME_PROBE']!)) : undefined
      if (before) writeDiagnostic(out, `${name}-contrast-before`, before)
      const shot = captureEvidence(out, name, phase, path => renderer.captureScreenshot(path), readPng)
      if (before) {
        const after = context(app!, renderer, count, measure(process.env['QUALIFICATION_FRAME_PROBE']!))
        writeDiagnostic(out, `${name}-contrast-after`, after)
        sameScene(before, after)
        const decoded = pixels(readCapturePng(shot.path), after)
        writeDiagnostic(out, `${name}-contrast-pixels`, { ...decoded, pngSHA256: createHash('sha256').update(readFileSync(shot.path)).digest('hex') })
      }
      return shot
    }
    const initial = await wait(() => snapshot(0))
    writeDiagnostic(out, 'initial-readiness', { phase, ...initial })
    writeDiagnostic(out, 'initial-styles', { case: process.env['QUALIFICATION_CASE'] ?? 'original', styles: styles(app) })
    assert(initial.children > 0 && initial.frames > 0 && !initial.containerConnected && initial.buttonConnected)
    renderedButton = app.document.querySelector('button')!
    record('before', { ...initial, screenshot: capture('count-0') })
    // Re-read all native state immediately before hit delivery; fail on stale target/geometry.
    const fresh = snapshot(0)
    assert(fresh !== undefined)
    assert.deepEqual({ id: fresh.id, bounds: fresh.bounds, window: fresh.window }, { id: initial.id, bounds: initial.bounds, window: initial.window })
    phase = 'native-input'
    record('input', { method: 'simulateClick', id: fresh.id, bounds: fresh.bounds, window: fresh.window, point: fresh.point })
    renderer.simulateClick(fresh.point.x, fresh.point.y)
    const incremented = await wait(() => {
      const value = snapshot(1)
      return value !== undefined && value.frames > initial.frames && !value.text.includes('Count: 0') && !value.painted.includes('Count: 0') ? value : undefined
    })
    record('after', { ...incremented, screenshot: capture('count-1') })
    phase = 'close-requested'
    app.onClose(request => {
      closeRequests++
      beforeClose = app!.document.body.childNodes.length
      record('close-request', { reason: request.reason, beforeClose, containerConnected: app!.container.isConnected, buttonConnected: renderedButton!.isConnected })
    })
    // This is the documented JS close route, preserving default exitOnClose:true.
    // On macOS the OS red close button bypasses JS ownership and is unsuitable here.
    await app.close()
    throw new Error('default app.close() returned instead of exiting')
  }
}
