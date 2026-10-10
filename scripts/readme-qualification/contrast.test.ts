// Synthetic decoded pixels / source controls only. No GPUI/native execution.
import { expect, test } from 'bun:test'
import { checkStyles, context, pixels, sameScene, type Context } from './contrast.ts'

const copy = <T>(value: T): T => structuredClone(value)
function scene(count = 0, scale = 1): Context {
  return { pid: 123, count, at: 100, frames: 9, window: { width: 100, height: 100 },
    probe: { pid: 123, windowId: 5, scale, window: { x: 0, y: 0, width: 100, height: 100 }, display: { x: 0, y: 0, width: 1000, height: 1000 } },
    styles: ['html', 'body', '.app', 'p', 'button'].map((selector, index) => ({ selector, id: [0, 1, 4, 2, 3][index]!, text: selector === 'p' ? `Count: ${count}` : selector === 'button' ? '+1' : '', color: '#000000', background: ['body', '.app'].includes(selector) ? '#ffffff' : '', ancestors: [] })),
    app: { id: 4, bounds: { x: 24, y: 24, width: 100, height: 40 } },
    regions: [{ label: 'body', id: 1, bounds: { x: 0, y: 0, width: 100, height: 100 } }, { label: `Count: ${count}`, id: 2, bounds: { x: 10, y: 10, width: 60, height: 20 } }, { label: '+1', id: 3, bounds: { x: 10, y: 40, width: 60, height: 20 } }] }
}
function image(scale = 1, count = 0) {
  return { width: 100 * scale, height: 100 * scale, pixel: (x: number, y: number) => {
    const dark = x >= (count ? 20 : 10) * scale && x < (count ? 23 : 13) * scale && y >= 10 * scale && y < 20 * scale || x >= 10 * scale && x < 13 * scale && y >= 40 * scale && y < 50 * scale
    return dark ? [0, 0, 0, 255] : [255, 255, 255, 255]
  } }
}

test('measured scale1/2 contrast and Count1 region pixel change', () => {
  for (const scale of [1, 2]) {
    const zero = pixels(image(scale, 0), scene(0, scale)), one = pixels(image(scale, 1), scene(1, scale))
    expect(zero.samples[1]!.dark).toBe(30 * scale * scale)
    expect(one.samples[1]!.inkSHA256).not.toBe(zero.samples[1]!.inkSHA256)
    expect(one.samples[2]!.inkSHA256).toBe(zero.samples[2]!.inkSHA256)
    expect(one.samples.every(v => v.white > 100)).toBe(true)
    expect(one.samples[0]!.label).toBe('body')
    expect(one.app.bounds.x + one.app.bounds.width).toBe(124)
    expect(one.appOverflow.right).toBe(24)
  }
})

test('uniform white/black, missing label ink, incidental unrelated variation all reject', () => {
  for (const rgb of [0, 255]) expect(() => pixels({ width: 100, height: 100, pixel: () => [rgb, rgb, rgb, 255] }, scene())).toThrow()
  const other = image()
  expect(() => pixels({ ...other, pixel: (x, y) => y < 30 ? [255, 255, 255, 255] : other.pixel(x, y) }, scene())).toThrow('foreground')
  expect(() => pixels({ ...other, pixel: (x, y) => x > 80 ? [x, y, 0, 255] : [255, 255, 255, 255] }, scene())).toThrow('foreground')
})

test('scale/dimensions/region provenance and wrong scene rejection', () => {
  for (const mutate of [
    (c: Context) => { c.probe.scale = NaN }, (c: Context) => { c.probe.scale = 2 },
    (c: Context) => { c.probe.pid = 999 }, (c: Context) => { c.probe.windowId = 0 },
    (c: Context) => { c.regions = [] }, (c: Context) => { c.regions[1]!.bounds.x = -1 },
    (c: Context) => { c.regions[1]!.bounds.width = 0 }, (c: Context) => { c.regions[1]!.bounds.y = 101 },
    (c: Context) => { c.regions[1]!.id = 0 }, (c: Context) => { c.regions[1]!.label = 'Count: 1' },
    (c: Context) => { c.regions[0]!.id = c.app.id }, (c: Context) => { c.regions[0]!.bounds.x = 1 },
    (c: Context) => { c.regions[1]!.bounds.x = 90 },
    (c: Context) => { c.app.id = 0 }, (c: Context) => { c.app.bounds.width = NaN },
    (c: Context) => { c.styles[3]!.text = 'Count: 1' },
  ]) { const c = scene(); mutate(c); expect(() => pixels(image(), c)).toThrow() }
  expect(() => pixels({ ...image(), pixel: () => [255, 255, 255, 0] }, scene())).toThrow('transparent')
})

test('explicit measured BODY is mandatory; app overflow is preserved without label clipping', () => {
  const original = scene()
  expect(pixels(image(), original).appOverflow.right).toBe(24)
  for (const mutate of [
    (c: Context) => { c.regions.shift() },
    (c: Context) => { c.regions[0]!.label = 'app' },
    (c: Context) => { c.styles[1]!.id++ },
    (c: Context) => { c.regions[0]!.bounds.width = 101 },
    (c: Context) => { c.regions[0]!.bounds.height = 0 },
    (c: Context) => { c.regions[0]!.bounds.x = -1 },
    (c: Context) => { c.regions[2]!.bounds.x = 90 },
  ]) { const changed = copy(original); mutate(changed); expect(() => pixels(image(), changed)).toThrow() }
})

test('context forwards the actual BODY ID and preserves separately measured app overflow', () => {
  // Harmless DOM/renderer-shaped replay, not a native mount or hit delivery.
  const values = scene().styles
  const elements = new Map(values.map(v => [v.selector, { nativeId: v.id, textContent: v.text, parentElement: null }]))
  const app = { document: { body: elements.get('body'), querySelector: (selector: string) => elements.get(selector) ?? null },
    window: { getComputedStyle: (element: { nativeId: number }) => ({ getPropertyValue: (key: string) => {
      const value = values.find(v => v.id === element.nativeId)!
      return key === 'color' ? value.color : value.background
    } }) } } as unknown as Parameters<typeof context>[0]
  const ids: number[] = []
  const boxes = new Map(scene().regions.map(v => [v.id, v.bounds]))
  boxes.set(4, scene().app.bounds)
  const renderer = { getElementBounds: (id: number) => { ids.push(id); return boxes.get(id) ?? null },
    getWindowSize: () => scene().window, getDebugFrameOverlayStats: () => ({ frames: 9 }) }
  const probe = { ...scene().probe, pid: process.pid }
  const measured = context(app, renderer, 0, probe)
  expect(ids).toEqual([1, 2, 3, 4])
  expect(measured.regions[0]).toEqual({ label: 'body', id: 1, bounds: scene().regions[0]!.bounds })
  expect(pixels(image(), measured).appOverflow.right).toBe(24)
  boxes.delete(1)
  expect(() => context(app, renderer, 0, probe)).toThrow('missing native bounds: body')
  boxes.set(1, scene().regions[0]!.bounds)
  elements.get('body')!.nativeId = 0
  expect(() => context(app, renderer, 0, probe)).toThrow('missing native ID: body')
})

test('missing/incorrect actual effective styles are rejected', () => {
  for (const mutate of [(c: Context) => { c.styles = [] }, (c: Context) => { c.styles[1]!.background = '' }, (c: Context) => { c.styles[2]!.background = '#000000' }, (c: Context) => { c.styles[3]!.color = '#ffffff' }, (c: Context) => { c.styles[4]!.text = 'fake' }]) {
    const c = scene(); mutate(c); expect(() => checkStyles(c.styles, 0)).toThrow()
  }
})

test('same-capture scene rejects stale/moved/resized/ambiguous identity or style', () => {
  const before = scene(), after = copy(before); after.at++; after.frames++
  expect(() => sameScene(before, after)).not.toThrow()
  for (const mutate of [(c: Context) => { c.pid++ }, (c: Context) => { c.probe.windowId++ }, (c: Context) => { c.probe.scale = 2 }, (c: Context) => { c.probe.window.x++ }, (c: Context) => { c.window.width++ }, (c: Context) => { c.frames = 0 }, (c: Context) => { c.at = 6000 }, (c: Context) => { c.at = 99 }, (c: Context) => { c.regions[1]!.bounds.x++ }, (c: Context) => { c.styles[3]!.color = '#ffffff' }, (c: Context) => { c.app.bounds.x++ }]) {
    const changed = copy(after); mutate(changed); expect(() => sameScene(before, changed)).toThrow()
  }
})
