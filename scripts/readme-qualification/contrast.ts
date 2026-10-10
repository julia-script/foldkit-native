// Additional diagnostic controls only; never a substitute for the original case.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import type { mountGpuix } from 'foldkit-gpuix'

type App = ReturnType<typeof mountGpuix>
type Box = { x: number; y: number; width: number; height: number }
type Size = { width: number; height: number }
export type Probe = { pid: number; windowId: number; scale: number; window: Box; display: Box }
export type Context = { pid: number; count: number; at: number; frames: number; window: Size; probe: Probe; styles: ReturnType<typeof styles>; regions: Array<{ label: string; id: number; bounds: Box }> }
type Image = { width: number; height: number; pixel(x: number, y: number): Array<number> }

export function styles(app: App) {
  return ['html', 'body', '.app', 'p', 'button'].map(selector => {
    const element = app.document.querySelector(selector)
    assert(element !== null, `missing style target: ${selector}`)
    const own = app.window.getComputedStyle(element)
    const ancestors = []
    for (let at = element.parentElement; at !== null; at = at.parentElement) {
      ancestors.push({ id: at.nativeId, tag: at.localName, background: app.window.getComputedStyle(at).getPropertyValue('background-color') })
    }
    return { selector, id: element.nativeId, text: element.textContent, color: own.getPropertyValue('color'), background: own.getPropertyValue('background-color'), ancestors }
  })
}

export function checkStyles(values: ReturnType<typeof styles>, count: number) {
  assert.deepEqual(values.map(v => v.selector), ['html', 'body', '.app', 'p', 'button'])
  assert(values.every(v => v.color === '#000000'), 'control foreground style is not black')
  for (const selector of ['body', '.app']) assert(values.find(v => v.selector === selector)?.background === '#ffffff', 'control background style is not white')
  assert.equal(values.find(v => v.selector === 'p')?.text, `Count: ${count}`, 'wrong count scene')
  assert.equal(values.find(v => v.selector === 'button')?.text, '+1', 'wrong button scene')
}

export function sameScene(before: Context, after: Context) {
  assert(before.pid > 0 && before.pid === after.pid && before.probe.pid === before.pid && after.probe.pid === after.pid, 'wrong owned PID')
  assert(before.probe.windowId > 0 && before.probe.windowId === after.probe.windowId, 'changed owned window')
  assert(before.frames > 0 && after.frames >= before.frames, 'stale completed frame')
  assert(after.at >= before.at && after.at - before.at <= 5000, 'stale capture context')
  assert.deepEqual({ count: after.count, window: after.window, styles: after.styles, regions: after.regions, probe: after.probe },
    { count: before.count, window: before.window, styles: before.styles, regions: before.regions, probe: before.probe }, 'scene/style/window/display changed during capture')
}

function deviceRegion(box: Box, size: Size, scale: number) {
  assert(Object.values(box).every(Number.isFinite) && box.width > 0 && box.height > 0, 'missing native region')
  // Require the entire region visible; no guessed clipping or requested geometry.
  assert(box.x >= 0 && box.y >= 0 && box.x + box.width <= size.width && box.y + box.height <= size.height, 'native region not fully visible')
  return { left: Math.ceil(box.x * scale), top: Math.ceil(box.y * scale), right: Math.floor((box.x + box.width) * scale), bottom: Math.floor((box.y + box.height) * scale) }
}

export function pixels(image: Image, context: Context) {
  checkStyles(context.styles, context.count)
  assert(context.pid === context.probe.pid && context.probe.windowId > 0, 'missing owned display/window evidence')
  const scale = context.probe.scale
  assert(Number.isFinite(scale) && scale > 0 && [image.width, image.height].every(v => Number.isInteger(v) && v > 0), 'invalid measured scale/image')
  assert(image.width === context.window.width * scale && image.height === context.window.height * scale, 'PNG/native logical dimensions differ at measured scale')
  assert.deepEqual(context.regions.map(v => v.label), ['app', `Count: ${context.count}`, '+1'], 'missing/wrong native regions')
  const samples = context.regions.map(({ label, id, bounds }) => {
    assert(id > 0, 'missing native region ID')
    const region = deviceRegion(bounds, context.window, scale)
    assert(region.right > region.left && region.bottom > region.top, 'empty device region')
    let white = 0, dark = 0
    const ink = createHash('sha256')
    for (let y = region.top; y < region.bottom; y++) for (let x = region.left; x < region.right; x++) {
      const rgba = image.pixel(x, y)
      assert(rgba.length === 4 && rgba.every(v => Number.isInteger(v) && v >= 0 && v <= 255) && rgba[3] === 255, 'invalid/transparent capture pixels')
      if (rgba.slice(0, 3).every(v => v >= 245)) white++
      const isInk = rgba.slice(0, 3).every(v => v <= 80)
      if (isInk) dark++
      ink.update(isInk ? '1' : '0')
    }
    const area = (region.right - region.left) * (region.bottom - region.top)
    // Labels need both foreground and background; an unrelated varied pixel is insufficient.
    assert(white > 100 && white / area > .5, `missing white background in ${label}`)
    if (label !== 'app') assert(dark >= 10 && dark / area < .5, `missing contrasting foreground in ${label}`)
    return { label, id, bounds, device: region, white, dark, area, inkSHA256: ink.digest('hex') }
  })
  return { pid: context.pid, count: context.count, scale, width: image.width, height: image.height, samples }
}

export function context(app: App, renderer: { getElementBounds(id: number): Box | null; getWindowSize(): Size; getDebugFrameOverlayStats(): { frames: number } }, count: number, probe: Probe): Context {
  const values = styles(app)
  const regions = ['.app', 'p', 'button'].map((selector, i) => {
    const element = app.document.querySelector(selector)!
    const bounds = renderer.getElementBounds(element.nativeId)
    assert(bounds !== null, `missing native bounds: ${selector}`)
    return { label: ['app', `Count: ${count}`, '+1'][i]!, id: element.nativeId, bounds }
  })
  return { pid: process.pid, count, at: Date.now(), frames: renderer.getDebugFrameOverlayStats().frames, window: renderer.getWindowSize(), probe, styles: values, regions }
}

export function measure(probePath: string): Probe {
  const child = Bun.spawnSync([probePath, String(process.pid)], { stdout: 'pipe', stderr: 'pipe', timeout: 5000 })
  assert.equal(child.exitCode, 0, `display/window probe failed: ${new TextDecoder().decode(child.stderr)}`)
  return JSON.parse(new TextDecoder().decode(child.stdout))
}
