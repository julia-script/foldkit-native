// Pure mocked control checks. These never launch or claim a native renderer.
import { expect, test } from 'bun:test'
import { frameViewport, nativeClick, visibleHit, visibleTarget, type Bounds, type Control, type Point, type State } from './cart-control.ts'

const windowBounds = { x: 0, y: 0, width: 760, height: 653 }
const placeOrder = { x: 350, y: 638, width: 120, height: 32 } // centre y654
function mock() {
  let time = 0
  let state: State = { text: ['Place Order'], painted: ['Place Order'] }
  let bounds: Bounds | null = placeOrder
  let targets = [{ id: 7, bounds: placeOrder }]
  const clicks: Point[] = []
  const records: unknown[] = []
  const control: Control = {
    query: async () => targets,
    bounds: async () => bounds,
    viewport: async () => windowBounds,
    state: async () => state,
    mouseClick: async point => { clicks.push(point); state = { text: ['Order placed successfully!'], painted: ['Order placed successfully!'] } },
    record: row => records.push(row),
    now: () => time,
    pause: async () => { time += 250 },
  }
  return { control, clicks, records, setBounds: (next: Bounds | null) => { bounds = next }, setTargets: (next: typeof targets) => { targets = next }, setState: (next: State) => { state = next } }
}
const success = (state: State) => state.text.includes('Order placed successfully!') && state.painted.includes('Order placed successfully!')

test('actual device frame and measured scale give653; outer titlebar height is excluded', () => {
  expect(frameViewport({ width: 1520, height: 1306 }, { scale: 2, window: { ...windowBounds, height: 681 } })).toEqual(windowBounds)
  expect(() => frameViewport({ width: 1520, height: 1306 }, { scale: 1, window: windowBounds })).toThrow('geometry mismatch')
  expect(() => frameViewport({ width: 1520, height: 1306 }, { scale: 0, window: windowBounds })).toThrow('backing scale')
})

test('centre654 is clipped to measured visible intersection inside653 and forwarded unchanged', async () => {
  const m = mock()
  expect(visibleHit(placeOrder, windowBounds)).toEqual({ x: 410, y: 645.5 })
  const after = await nativeClick(m.control, 'Place Order', success)
  expect(m.clicks).toEqual([{ x: 410, y: 645.5 }])
  expect(success(after)).toBe(true)
  expect(m.records).toMatchObject([
    { kind: 'target', id: 7, bounds: placeOrder, viewport: windowBounds, point: { x: 410, y: 645.5 } },
    { kind: 'delivered', id: 7, point: { x: 410, y: 645.5 } },
    { kind: 'observed', id: 7, after },
  ])
})

test('fully invisible, empty, and boundary sliver targets deliver no input', async () => {
  for (const bounds of [{ ...placeOrder, y: 654 }, { ...placeOrder, width: 0 }, { ...placeOrder, y: 652.5 }]) {
    const m = mock()
    m.setBounds(bounds)
    m.setTargets([{ id: 7, bounds }])
    await expect(nativeClick(m.control, 'Place Order', success)).rejects.toThrow('did not become ready')
    expect(m.clicks).toEqual([])
  }
})

test('missing target, stale ID, null bounds and stale layout are bounded failures', async () => {
  for (const fault of ['missing', 'id', 'null', 'layout']) {
    const m = mock()
    if (fault === 'missing') m.setTargets([])
    if (fault === 'null') m.setBounds(null)
    if (fault === 'layout') m.setBounds({ ...placeOrder, y: 640 })
    if (fault === 'id') {
      let query = 0
      m.control.query = async () => [{ id: ++query, bounds: placeOrder }]
    }
    await expect(visibleTarget(m.control, 'Place Order')).rejects.toThrow('did not become ready')
    expect(m.clicks).toEqual([])
  }
})

test('unpainted readiness and nonfinite geometry fail before native input', async () => {
  const m = mock()
  m.setState({ text: ['Place Order'], painted: [] })
  await expect(nativeClick(m.control, 'Place Order', success)).rejects.toThrow('did not become ready')
  expect(m.clicks).toEqual([])
  expect(() => visibleHit({ ...placeOrder, y: NaN }, windowBounds)).toThrow('nonfinite')
})

test('native delivery without observable state change fails; acknowledgment is insufficient', async () => {
  const m = mock()
  m.control.mouseClick = async point => { m.clicks.push(point) }
  await expect(nativeClick(m.control, 'Place Order', success)).rejects.toThrow('native state change')
  expect(m.clicks).toEqual([{ x: 410, y: 645.5 }])
  expect(m.records).toHaveLength(2)
})

test('already satisfied postcondition and native input error cannot masquerade as delivery', async () => {
  const satisfied = mock()
  satisfied.setState({ text: ['Place Order', 'Order placed successfully!'], painted: ['Place Order', 'Order placed successfully!'] })
  await expect(nativeClick(satisfied.control, 'Place Order', success)).rejects.toThrow('already true')
  expect(satisfied.clicks).toEqual([])
  const failed = mock()
  failed.control.mouseClick = async () => { throw new Error('native input unavailable') }
  await expect(nativeClick(failed.control, 'Place Order', success)).rejects.toThrow('native input unavailable')
  expect(failed.records).toHaveLength(1)
})
