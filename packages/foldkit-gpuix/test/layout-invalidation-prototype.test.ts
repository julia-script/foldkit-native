// LOCAL PROTOTYPE controls: fake GPUI reads are not paint/speed measurements.
import { afterEach, expect, test } from 'bun:test'
import type { StyleDesc } from '@gpuix/native/host'
import { Schema } from 'effect'
import { loadExample } from '../../../examples/support/example.ts'
import { STORAGE_KEY } from '../../../examples/pixel-art/constant.ts'
import { createEmptyGrid } from '../../../examples/pixel-art/grid.ts'
import { SavedCanvasJsonString } from '../../../examples/pixel-art/model.ts'
import { autonomousStyle, paintBatch, paintStyle } from '../src/layout-invalidation-prototype.ts'
import { type Headless, mountHeadless } from './support.ts'

const opened: Array<Headless> = []
afterEach(() => { for (const app of opened.splice(0).reverse()) app.close() })
const white = '#ffffff'
const ink = '#262427'
const oldStyle = { width: 50, backgroundColor: white }

test('full style replacement rejects removed geometry and unknown/opaque values', () => {
  expect(paintStyle(oldStyle, { ...oldStyle, backgroundColor: ink })).toBe(true)
  expect(paintStyle(oldStyle, { ...oldStyle })).toBe(true)
  for (const next of [
    { backgroundColor: ink }, { ...oldStyle, width: 60 },
    { ...oldStyle, fontSize: 18 }, { ...oldStyle, backgroundColor: 'transparent' },
    { ...oldStyle, background: 'red' }, { ...oldStyle, opacity: 0.5 },
    { ...oldStyle, futureLayout: 1 }, { ...oldStyle, width: NaN },
  ]) expect(paintStyle(oldStyle, next as StyleDesc)).toBe(false)
  expect(paintStyle(undefined, oldStyle)).toBe(false)
  expect(paintStyle({ ...oldStyle, futureLayout: 1 } as StyleDesc, oldStyle)).toBe(false)
  expect(autonomousStyle({ hover: { backgroundColor: 'red' } })).toBe(false)
  for (const style of [{ hover: { width: 99 } }, { active: { fontSize: 30 } }, { hover: { futureLayout: 1 } }, { futureLayout: 1 }]) {
    expect(autonomousStyle(style as StyleDesc)).toBe(true)
  }
})

test('mixed/earlier geometric commands cannot be erased by later color commands or resets', () => {
  const batch = paintBatch()
  batch.style(true)
  expect(batch.moved(1, false)).toBe(false)
  expect(batch.moved(2, false)).toBe(true)
  expect(batch.moved(1, true)).toBe(true)
  expect(batch.moved(0, true)).toBe(false)
  batch.reset()
  expect(batch.moved(1, false)).toBe(true)
  batch.style(false); batch.style(true)
  expect(batch.moved(2, false)).toBe(true)
})

const make = (baseline = false, css = '') => {
  let clock = 0
  let app: Headless | undefined
  app = mountHeadless({ css, now: () => clock, onSynced: ({ mutations }) => {
    // Original main policy, through the real host's explicit invalidation seam.
    if (baseline && mutations > 0) app?.host.relayout()
  } })
  opened.push(app)
  const cell = app.document.createElement('div')
  cell.setAttribute('style', `width:50px;background-color:${white}`)
  app.container.appendChild(cell as never)
  app.host.flush()
  app.gpui.setBounds(cell.nativeId, { x: 10, y: 10, width: 50, height: 50 })
  const frame = () => { clock += 20; app!.host.drawn() }
  for (let i = 0; i < 8; i++) frame()
  cell.getBoundingClientRect()
  return { app, cell, frame, color: () => { cell.setAttribute('style', `width:50px;background-color:${ink}`); app!.host.flush() } }
}

test('color-only batch preserves payload, geometry and age refresh while reducing bounded fake queries', () => {
  const run = (baseline: boolean) => {
    const rig = make(baseline)
    const start = rig.app.gpui.batches.length
    const reads = rig.app.host.geometry().reads
    rig.color()
    for (let i = 0; i < 4; i++) { rig.frame(); expect(rig.cell.getBoundingClientRect().width).toBe(50) }
    const result = { commands: rig.app.gpui.batches.slice(start), reads: rig.app.host.geometry().reads - reads }
    for (let i = 0; i < 30; i++) rig.frame()
    rig.cell.getBoundingClientRect()
    expect(rig.app.host.geometry().reads).toBeGreaterThan(reads + result.reads)
    return result
  }
  const original = run(true)
  const candidate = run(false)
  expect(candidate.commands).toEqual(original.commands)
  expect(original.reads).toBeGreaterThan(0)
  expect(candidate.reads).toBe(0)
})

for (const change of ['ratio', 'remove', 'explicit height', 'reintroduce', 'unchanged'] as const) {
  test(`settled aspect metadata preserves original fitting and invalidation: ${change}`, async () => {
    const run = async (baseline: boolean) => {
      const { app, cell } = make(baseline)
      const cycles = async (count: number) => {
        for (let i = 0; i < count; i++) {
          await new Promise(resolve => setTimeout(resolve, 0))
          app.host.frame(); app.host.flush(); app.host.drawn()
        }
      }
      cell.setAttribute('style', `width:100px;aspect-ratio:1;background-color:${white}`)
      app.host.flush()
      app.gpui.setBounds(cell.nativeId, { x: 10, y: 10, width: 100, height: 100 })
      await cycles(8)
      expect(app.gpui.node(cell.nativeId).style['height']).toBe(100)
      if (change === 'reintroduce') {
        cell.setAttribute('style', `width:100px;height:100px;background-color:${white}`)
        app.host.flush(); await cycles(8)
      }
      const next = change === 'ratio' || change === 'reintroduce' ? 'aspect-ratio:2'
        : change === 'remove' || change === 'explicit height' ? 'height:100px'
        : 'aspect-ratio:1'
      const before = app.gpui.batches.length
      const reads = app.host.geometry().reads
      cell.setAttribute('style', `width:100px;${next}${change === 'explicit height' ? ';aspect-ratio:2' : ''};background-color:${white}`)
      app.host.flush()
      // Even a changed ratio initially sends the old fitted height.
      expect(app.gpui.node(cell.nativeId).style['height']).toBe(100)
      await cycles(4)
      cell.getBoundingClientRect()
      return { height: app.gpui.node(cell.nativeId).style['height'], commands: app.gpui.batches.slice(before), reads: app.host.geometry().reads - reads }
    }
    const original = await run(true)
    const candidate = await run(false)
    expect(candidate.height).toBe(original.height)
    expect(candidate.height).toBe(change === 'ratio' || change === 'reintroduce' ? 50 : 100)
    expect(candidate.commands).toEqual(original.commands)
    if (change !== 'unchanged') expect(candidate.reads).toBeGreaterThan(0)
    else expect(candidate.reads).toBe(0)
  })
}

test('global aspect fallback rejects a cached height until the current ratio can be fitted', async () => {
  const { app, cell, color } = make()
  const other = app.document.createElement('div')
  other.setAttribute('style', `width:100px;aspect-ratio:1;background-color:${white}`)
  app.document.body.appendChild(other); app.host.flush()
  app.gpui.setBounds(other.nativeId, { x: 100, y: 100, width: 100, height: 100 })
  await app.settle(); await app.settle()
  expect(app.gpui.node(other.nativeId).style['height']).toBe(100)
  app.gpui.setBounds(other.nativeId, { x: 100, y: 100, width: 0, height: 100 })
  app.host.relayout(); await app.settle(); await app.settle()
  other.style.setProperty('aspect-ratio', '2'); app.host.flush()
  await app.settle(); await app.settle()
  expect(app.gpui.node(other.nativeId).style['height']).toBe(100)
  let reads = app.host.geometry().reads
  color(); await app.settle(); cell.getBoundingClientRect()
  expect(app.host.geometry().reads).toBeGreaterThan(reads)
  app.gpui.setBounds(other.nativeId, { x: 100, y: 100, width: 100, height: 100 })
  app.host.relayout(); await app.settle(); await app.settle()
  expect(app.gpui.node(other.nativeId).style['height']).toBe(50)
  reads = app.host.geometry().reads
  cell.style.setProperty('background-color', white); app.host.flush()
  await app.settle(); cell.getBoundingClientRect()
  expect(app.host.geometry().reads).toBe(reads)
})

for (const action of ['width removal', 'text', 'structure', 'listener', 'custom prop', 'reorder'] as const) {
  test(`unknown/mixed batch retains geometry invalidation: ${action}`, () => {
    const { app, cell, frame, color } = make()
    const reads = app.host.geometry().reads
    cell.setAttribute('style', `width:50px;background-color:${ink}`)
    if (action === 'width removal') cell.setAttribute('style', `background-color:${ink}`)
    if (action === 'text') cell.textContent = 'changes intrinsic size'
    if (action === 'structure') cell.appendChild(app.document.createElement('div'))
    if (action === 'listener') cell.addEventListener('click', () => {})
    if (action === 'custom prop') cell.setAttribute('aria-label', 'new name')
    if (action === 'reorder') app.document.body.appendChild(cell)
    app.host.flush(); frame(); cell.getBoundingClientRect()
    expect(app.host.geometry().reads).toBeGreaterThan(reads)
    const previous = app.host.geometry().reads
    color(); frame(); cell.getBoundingClientRect()
    // A color batch cannot clear the preceding geometry settling interval.
    expect(app.host.geometry().reads).toBeGreaterThan(previous)
  })
}

for (const feature of ['motion sibling', 'hover sibling', 'active sibling', 'unresolved aspect', 'image', 'input', 'anchored', 'virtual-list', 'unknown feature'] as const) {
  test(`global autonomous geometry fallback: ${feature}`, () => {
    const { app, cell, frame, color } = make(false, '.dynamic-hover:hover { width:200px } .dynamic-active:active { height:200px }')
    const other = app.document.createElement(feature === 'image' ? 'img' : feature === 'input' ? 'input' : 'div')
    if (feature === 'motion sibling') other.setAttribute('data-fn-motion', '{"duration":1000,"width":200}')
    if (feature === 'hover sibling') other.setAttribute('class', 'dynamic-hover')
    if (feature === 'active sibling') other.setAttribute('class', 'dynamic-active')
    if (feature === 'unresolved aspect') other.setAttribute('style', 'aspect-ratio:1')
    if (feature === 'anchored') other.setAttribute('data-fn-anchored', '{}')
    if (feature === 'virtual-list') other.setAttribute('data-fn-virtual-list', '{"itemCount":1,"estimatedItemHeight":20}')
    if (feature === 'unknown feature') other.setAttribute('data-fn-future', '')
    app.document.body.appendChild(other)
    app.host.flush()
    for (let i = 0; i < 8; i++) frame()
    cell.getBoundingClientRect()
    const reads = app.host.geometry().reads
    color(); frame(); cell.getBoundingClientRect()
    expect(app.host.geometry().reads).toBeGreaterThan(reads)
  })
}

test('failed color flush preserves raw error and forces conservative retry; re-entrant geometry survives', () => {
  const { app, cell, frame, color } = make()
  const apply = app.fake.renderer.applyBatch
  const failure = new Error('original batch error')
  app.fake.renderer.applyBatch = () => { throw failure }
  expect(color).toThrow(failure)
  app.fake.renderer.applyBatch = apply
  const reads = app.host.geometry().reads
  app.host.flush(); frame(); cell.getBoundingClientRect()
  expect(app.host.geometry().reads).toBeGreaterThan(reads)
  let reentrant = true
  app.fake.renderer.applyBatch = function (json) {
    const result = apply.call(this, json)
    if (reentrant) { reentrant = false; cell.style.setProperty('width', '90px') }
    return result
  }
  cell.style.setProperty('background-color', white); app.host.flush(); app.host.flush()
  app.gpui.setBounds(cell.nativeId, { x: 10, y: 10, width: 90, height: 50 })
  frame(); expect(cell.getBoundingClientRect().width).toBe(90)
})

test('unknown inspection fails closed and failed detach preserves the original error without cross-host metadata', () => {
  const { app, cell, frame, color } = make()
  const names = cell.getAttributeNames
  cell.getAttributeNames = () => { throw new Error('unknown inspection') }
  const reads = app.host.geometry().reads
  color(); frame(); cell.getBoundingClientRect()
  expect(app.host.geometry().reads).toBeGreaterThan(reads)
  cell.getAttributeNames = names
  const failure = new Error('owned detach error')
  const apply = app.fake.renderer.applyBatch
  app.fake.renderer.applyBatch = () => { throw failure }
  expect(() => app.close()).toThrow(failure)
  // Main already leaves a scheduled sync after a failing native teardown.
  // Restore the owned fake and drain its retained batch, not that shared behavior.
  app.fake.renderer.applyBatch = apply
  app.host.flush()
  const next = make()
  const before = next.app.host.geometry().reads
  next.cell.style.setProperty('width', '70px'); next.app.host.flush(); next.frame()
  next.cell.getBoundingClientRect()
  expect(next.app.host.geometry().reads).toBeGreaterThan(before)
})

test('actual headless Pixel32 steady stroke: exact payloads/Ink/events/boxes match original policy', async () => {
  const example = await loadExample('pixel-art')
  const run = async (baseline: boolean, sizePalette = true) => {
    let clock = 0
    let app: Headless | undefined
    app = mountHeadless({ css: example.css, viewport: { width: 1100, height: 820 }, now: () => clock,
      onSynced: ({ mutations }) => { if (baseline && mutations > 0) app?.host.relayout() } })
    opened.push(app)
    app.window.localStorage.setItem(STORAGE_KEY, Schema.encodeSync(SavedCanvasJsonString)({
      grid: createEmptyGrid(32), gridSize: 32, selectedColorIndex: 0, paletteThemeIndex: 0,
    }))
    example.start(app.container)
    await app.settle(); await app.settle()
    const grid = app.document.querySelector('.cursor-crosshair')!
    app.gpui.setBounds(grid.nativeId, { x: 100, y: 100, width: 640, height: 640 })
    grid.children.forEach((row, y) => {
      app!.gpui.setBounds(row.nativeId, { x: 100, y: 100 + y * 20, width: 640, height: 20 })
      row.children.forEach((cell, x) => app!.gpui.setBounds(cell.nativeId, { x: 100 + x * 20, y: 100 + y * 20, width: 20, height: 20 }))
    })
    // The fake has no layout engine. Give the real palette's aspect boxes
    // positive, fixed widths as well; otherwise they must remain unresolved.
    if (sizePalette) app.document.querySelectorAll('*').filter(node => node !== grid && node.getAttribute('class')?.includes('aspect-square')).forEach((node, index) => {
      app!.gpui.setBounds(node.nativeId, { x: 800 + index * 20, y: 20, width: 20, height: 20 })
    })
    app.host.relayout()
    const settle = async () => {
      for (let i = 0; i < 4; i++) {
        await new Promise(resolve => setTimeout(resolve, 0))
        clock += 10; app!.host.frame(); app!.host.flush(); app!.host.drawn()
      }
    }
    await settle(); await settle(); await settle()
    const cell = (x: number) => grid.children[0]!.children[x]!
    app.host.dispatch({ eventType: 'mouseDown', elementId: cell(0).nativeId, x: 110, y: 110, button: 0 })
    await settle(); await settle()
    const start = app.gpui.batches.length
    const reads = app.host.geometry().reads
    const targets: Array<number> = []
    const boxes: Array<{ x: number; width: number }> = []
    for (let x = 1; x <= 3; x++) {
      cell(x).addEventListener('mouseenter', () => targets.push(x))
    }
    app.host.flush(); await settle(); await settle()
    const steadyStart = app.gpui.batches.length
    const steadyReads = app.host.geometry().reads
    for (let x = 1; x <= 3; x++) {
      app.host.dispatch({ eventType: 'mouseMove', elementId: cell(0).nativeId, x: 110 + x * 20, y: 110, pressedButton: 0 })
      await settle()
      const box = cell(x).getBoundingClientRect()
      boxes.push({ x: box.x, width: box.width })
      expect(app.gpui.node(cell(x).nativeId).style['backgroundColor']).toBe(ink)
    }
    expect(targets).toEqual([1, 2, 3])
    expect(app.gpui.node(cell(0).nativeId).style['backgroundColor']).toBe(ink)
    const result = { commands: app.gpui.batches.slice(steadyStart), boxes, targets, reads: app.host.geometry().reads - steadyReads }
    expect(app.gpui.batches.length).toBeGreaterThan(start)
    expect(app.host.geometry().reads).toBeGreaterThanOrEqual(reads)
    app.close(); opened.splice(opened.indexOf(app), 1)
    return result
  }
  const baseline = await run(true)
  const candidate = await run(false)
  expect(candidate.commands).toEqual(baseline.commands)
  expect(candidate.boxes).toEqual(baseline.boxes)
  expect(candidate.targets).toEqual(baseline.targets)
  expect(candidate.reads).toBeLessThan(baseline.reads)
  const unresolvedBaseline = await run(true, false)
  const unresolvedCandidate = await run(false, false)
  expect(unresolvedCandidate.commands).toEqual(unresolvedBaseline.commands)
  expect(unresolvedCandidate.reads).toBe(unresolvedBaseline.reads)
  console.log('Pixel32 fake query comparison', JSON.stringify({ baseline: baseline.reads, candidate: candidate.reads, batches: candidate.commands.length }))
}, 15000)
