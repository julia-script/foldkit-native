// Disposable hosted diagnostics. Original tests and production code stay intact.
import assert from 'node:assert/strict'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import type { EventPayload } from '@gpuix/native'
import { TestRenderer } from '@gpuix/native/testing'

import { loadExample } from '../examples/support/example.ts'
import type { Metal } from '../examples/support/harness.ts'
import { LIST_HEIGHT } from '../examples/big-list/main.ts'
import { NativeElement, attachGpuix } from '../packages/foldkit-gpuix/src/index.ts'
import { readPng } from '../test/support/png.ts'

const [probe, mode] = process.argv.slice(2)
assert.ok(['big-list', 'cart', 'stroke', 'listbox'].includes(probe ?? ''))
assert.ok(mode === 'requested' || mode === 'native')
assert.equal(process.platform, 'darwin', 'this diagnostic must exercise macOS Metal')
const id = probe === 'cart' ? 'shopping-cart' : probe === 'big-list' ? 'big-list' : 'pixel-art'
const out = process.env['FOLDKIT_NATIVE_EVIDENCE']!
assert.ok(out, 'each child requires its own evidence directory')
mkdirSync(out, { recursive: true })
const emit = (name: string, value: unknown) => console.log('[DIAG-a535]', name, JSON.stringify(value))
const nativeId = (element: Element) => (element as unknown as NativeElement).nativeId

// Same text lookup as the original example harness; no alternate click target.
const find = (document: Document, text: string): Element => {
  const all = Array.from(document.body.querySelectorAll('*'))
  const labelled = all.find(el => el.getAttribute('aria-label') === text || el.getAttribute('placeholder') === text)
  const matches = all.filter(el => el.textContent?.trim() === text)
  const result = labelled ?? matches.find(el => !matches.some(other => other !== el && el.contains(other)))
  assert.ok(result, `nothing shows ${text}`)
  return result
}

// Only control variable: attach's initial DOM viewport. Renderer construction,
// CSS, example start, input methods and four-cycle settle match the helper.
const openDiagnosticViewport = async (): Promise<Metal> => {
  const example = await loadExample(id)
  const renderer = new TestRenderer({ width: example.meta.width, height: example.meta.height })
  const actual = renderer.getWindowSize()
  const viewport = mode === 'native' ? actual : { width: example.meta.width, height: example.meta.height }
  const attached = attachGpuix(renderer, { css: example.css, viewport })
  const settle = async () => {
    for (let i = 0; i < 4; i++) {
      await new Promise(resolve => setTimeout(resolve, 0))
      attached.host.frame()
      attached.host.flush()
      renderer.flush()
      attached.host.drawn()
      renderer.dispatchNativeEvents()
    }
  }
  const document = attached.document as unknown as Document
  const boundsOf = (element: Element) => {
    const bounds = renderer.getElementBounds(nativeId(element))
    assert.ok(bounds, 'target must be laid out')
    return bounds
  }
  const bounds = (target: string | Element) => boundsOf(typeof target === 'string' ? find(document, target) : target)
  try {
    example.start(attached.container)
    await settle()
    await settle()
  } catch (error) {
    attached.detach()
    throw error
  }
  return {
    example, renderer, document, container: attached.container,
    localStorage: attached.window.localStorage as unknown as Storage,
    bounds, boundsOf, settle, frame: settle,
    painted: () => renderer.getPaintedText(),
    click: async target => {
      await settle()
      const box = bounds(target)
      renderer.nativeSimulateClick(box.x + box.width / 2, box.y + box.height / 2)
      await settle()
    },
    keys: async keys => { await settle(); renderer.simulateKeystrokes(keys); await settle() },
    screenshot: name => {
      const path = join(out, `${id}-${name}.png`)
      renderer.captureScreenshot(path)
      return { path, ...readPng(path) }
    },
    close: async () => attached.detach(),
  }
}

const sizes = (app: Metal) => ({
  requested: { width: app.example.meta.width, height: app.example.meta.height },
  native: app.renderer.getWindowSize(),
  dom: { width: app.document.defaultView!.innerWidth, height: app.document.defaultView!.innerHeight },
  documentElement: { width: app.document.documentElement.clientWidth, height: app.document.documentElement.clientHeight },
})
const shot = (app: Metal, name: string) => {
  const image = app.screenshot(name)
  const size = sizes(app)
  emit('frame', { name, ...size, image: { path: image.path, width: image.width, height: image.height },
    densityNative: { x: image.width / size.native.width, y: image.height / size.native.height },
    ratioRequested: { x: image.width / size.requested.width, y: image.height / size.requested.height } })
  return image
}
const centre = (box: { x: number; y: number; width: number; height: number }) => ({ x: box.x + box.width / 2, y: box.y + box.height / 2 })
const target = (app: Metal, element: Element) => {
  const box = app.boundsOf(element)
  const point = centre(box)
  const actual = app.renderer.getWindowSize()
  const ancestors = []
  for (let el: Element | null = element; el !== null; el = el.parentElement) {
    const retained = app.renderer.getElement(nativeId(el))
    ancestors.push({ id: nativeId(el), tag: el.tagName, role: el.getAttribute('role'),
      box: app.renderer.getElementBounds(nativeId(el)), style: retained?.style,
      events: retained === undefined ? [] : [...retained.events] })
  }
  return { id: nativeId(element), box, point,
    centreInsideNative: point.x >= 0 && point.y >= 0 && point.x < actual.width && point.y < actual.height,
    ancestors }
}

// Observe dispatch on the target ancestry without adding listeners (adding
// listeners can itself change native hit registrations and invalidate a probe).
const observeDom = (element: Element) => {
  const events: Array<{ type: string; dispatchedOn: number }> = []
  const restores: Array<() => void> = []
  for (let el: Element | null = element; el !== null; el = el.parentElement) {
    const node = el
    const original = node.dispatchEvent
    node.dispatchEvent = event => {
      events.push({ type: event.type, dispatchedOn: nativeId(node) })
      return original.call(node, event)
    }
    restores.push(() => { node.dispatchEvent = original })
  }
  return { events, restore: () => { for (const restore of restores.reverse()) restore() } }
}

let app: Metal | undefined
let restoreEvents: (() => void) | undefined
let code = 0
try {
  app = await openDiagnosticViewport()
  emit('initial', { probe, mode, ...sizes(app), retained: app.renderer.getRetainedElementCount() })
  const events: EventPayload[] = []
  // gpuix0.10 keeps this callback private in its types but uses a JS property.
  // The diagnostic observes and forwards exactly once; no event is injected.
  const dispatcher = app.renderer as unknown as { dispatchEvent: (event: EventPayload) => void }
  const original = dispatcher.dispatchEvent
  assert.equal(typeof original, 'function', 'pinned gpuix event callback shape changed')
  dispatcher.dispatchEvent = event => { events.push({ ...event }); original.call(app!.renderer, event) }
  restoreEvents = () => { dispatcher.dispatchEvent = original }
  const eventSnapshot = () => {
    writeFileSync(join(out, 'native-events.json'), JSON.stringify(events, null, 2))
    return events
  }

  if (probe === 'big-list') {
    const box = app.bounds('Tracks')
    shot(app, 'initial')
    emit('big-list', { target: target(app, find(app.document, 'Tracks')), expected: LIST_HEIGHT,
      delta: Math.abs(box.height - LIST_HEIGHT), events: eventSnapshot() })
    assert.ok(Math.abs(box.height - LIST_HEIGHT) <= 4, 'original Big List height assertion')
  } else if (probe === 'cart') {
    const apple = app.bounds('Apple')
    app.renderer.nativeSimulateMouseMove(apple.x + 4, apple.y + 4)
    await app.settle()
    await app.click('Add to Cart')
    await app.click('Add to Cart')
    await app.click('Cart (2)')
    await app.click('+')
    await app.click('Proceed to Checkout')
    await app.click('Special delivery instructions (optional)...')
    await app.keys('r i n g space t w i c e')
    assert.equal(app.document.querySelector('textarea')!.value, 'ring twice')
    const button = find(app.document, 'Place Order')
    emit('cart-before', target(app, button))
    shot(app, 'before-order')
    events.length = 0
    const dom = observeDom(button)
    try { await app.click('Place Order') } finally { dom.restore() }
    const state = () => ({ domText: app!.document.body.textContent,
      retainedText: app!.renderer.getAllText(), paintedText: app!.painted() })
    const after = state()
    emit('cart-after', { ...after, domEvents: dom.events, events: eventSnapshot() })
    shot(app, 'after-order')
    // Additional readiness observation cannot replace the original result.
    await app.settle()
    emit('cart-extra-settle', state())
    assert.ok(after.paintedText.includes('Order placed successfully!'), 'original Cart painted assertion')
  } else if (probe === 'stroke') {
    const grid = app.document.querySelector('.cursor-crosshair')!
    const cell = (x: number, y: number) => grid.children[y]!.children[x]! as HTMLElement
    const point = (x: number, y: number) => centre(app!.boundsOf(cell(x, y)))
    shot(app, 'editor')
    const row = [2, 3, 4, 5, 6, 7, 8, 9].map(x => point(x, 4))
    const first = row[0]!
    app.renderer.nativeSimulateMouseMove(first.x, first.y)
    await app.settle()
    app.renderer.nativeSimulateMouseDown(first.x, first.y)
    await app.settle()
    for (const p of row.slice(1)) { app.renderer.nativeSimulateMouseMove(p.x, p.y, 0); await app.settle() }
    const last = row.at(-1)!
    app.renderer.nativeSimulateMouseUp(last.x, last.y)
    await app.settle()
    const image = shot(app, 'stroke')
    const p = point(5, 4)
    const size = sizes(app)
    const sample = (xRatio: number, yRatio: number) => {
      const x = Math.round(p.x * xRatio), y = Math.round(p.y * yRatio)
      if (x < 0 || y < 0 || x >= image.width || y >= image.height) return { x, y, hex: null }
      return { x, y, hex: '#' + image.pixel(x, y).map(v => v.toString(16).padStart(2, '0')).join('') }
    }
    const originalPixel = sample(image.width / size.requested.width, image.width / size.requested.width)
    const actualPixel = sample(image.width / size.native.width, image.height / size.native.height)
    emit('stroke', { board: target(app, grid), cell: target(app, cell(5, 4)), point: p,
      domColours: [2, 3, 4, 5, 6, 7, 8, 9].map(x => cell(x, 4).style.backgroundColor),
      originalPixel, actualPixel, events: eventSnapshot() })
    for (const x of [2, 3, 4, 5, 6, 7, 8, 9]) assert.equal(cell(x, 4).style.backgroundColor, '#262427')
    assert.equal(originalPixel.hex, '#262427', 'original requested-width Pixel assertion')
  } else {
    await app.click('Syntax')
    const panel = app.document.querySelector('[role=listbox]')!
    const ocean = find(app.document, 'Ocean')
    emit('listbox-before', { trigger: target(app, find(app.document, 'Syntax')), panel: target(app, panel),
      placement: panel.getAttribute('data-placement'), style: panel.getAttribute('style'),
      ocean: target(app, ocean), paintedText: app.painted() })
    shot(app, 'listbox-before')
    events.length = 0
    const dom = observeDom(ocean)
    try { await app.click('Ocean') } finally { dom.restore() }
    const stillOpen = app.document.querySelector('[role=listbox]') !== null
    emit('listbox-after', { stillOpen, domEvents: dom.events, events: eventSnapshot(),
      domText: app.document.body.textContent, retainedText: app.renderer.getAllText(), paintedText: app.painted() })
    shot(app, 'listbox-after')
    assert.equal(stillOpen, false, 'original Listbox dismissal assertion')
  }
} catch (error) {
  console.error(error)
  code = 1
} finally {
  restoreEvents?.()
  if (app !== undefined) {
    try {
      await app.close()
      const retained = app.renderer.getRetainedElementCount()
      emit('cleanup', { retained, processBoundary: 'child exits after detach; no shared renderer between probes' })
      assert.equal(retained, 0, 'own native nodes must be released')
    } catch (error) { console.error('diagnostic cleanup failed', error); code = 1 }
  }
}
// Example start returns void and may retain FoldKit runtime work (#56). Each
// observation has its own child process; exit is an explicit isolation boundary.
process.exit(code)
