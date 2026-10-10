import assert from 'node:assert/strict'

export type Bounds = { x: number; y: number; width: number; height: number }
export type Point = { x: number; y: number }
export type State = { text: string[]; painted: string[] }
export type Target = { id: number; bounds?: Bounds }
export type FrameMetadata = { scale: number; window: Bounds }
export type Control = {
  query(label: string): Promise<Target[]>
  bounds(id: number): Promise<Bounds | null>
  viewport(): Promise<Bounds>
  state(): Promise<State>
  mouseClick(point: Point): Promise<void>
  record(row: unknown): void
  now(): number
  pause(): Promise<void>
}

export function frameViewport(pixels: { width: number; height: number }, metadata: FrameMetadata): Bounds {
  assert(Number.isFinite(metadata.scale) && metadata.scale > 0, 'missing actual display backing scale')
  const viewport = { x: 0, y: 0, width: pixels.width / metadata.scale, height: pixels.height / metadata.scale }
  assert(viewport.width > 0 && viewport.height > 0 && Number.isInteger(viewport.width) && Number.isInteger(viewport.height), 'invalid logical native frame')
  assert(Object.values(metadata.window).every(Number.isFinite) && Math.abs(viewport.width - metadata.window.width) <= 1 && viewport.height <= metadata.window.height, 'frame/owned-window geometry mismatch')
  return viewport
}

export function visibleHit(target: Bounds, viewport: Bounds): Point | undefined {
  assert(Object.values(target).every(Number.isFinite) && Object.values(viewport).every(Number.isFinite), 'nonfinite native geometry')
  assert(viewport.width > 0 && viewport.height > 0, 'empty native viewport')
  if (target.width <= 0 || target.height <= 0) return
  const left = Math.max(target.x, viewport.x)
  const top = Math.max(target.y, viewport.y)
  const right = Math.min(target.x + target.width, viewport.x + viewport.width)
  const bottom = Math.min(target.y + target.height, viewport.y + viewport.height)
  // Do not click a subpixel sliver or the window/target boundary.
  if (right - left < 2 || bottom - top < 2) return
  return { x: (left + right) / 2, y: (top + bottom) / 2 }
}

export async function waitFor<T>(control: Pick<Control, 'now' | 'pause'>, name: string, read: () => Promise<T | undefined>, timeout = 5000): Promise<T> {
  const deadline = control.now() + timeout
  do {
    const value = await read()
    if (value !== undefined) return value
    await control.pause()
  } while (control.now() < deadline)
  throw new Error(`${name} did not become ready in ${timeout}ms`)
}

export async function visibleTarget(control: Control, label: string) {
  return waitFor(control, `visible painted target ${label}`, async () => {
    const nodes = await control.query(label)
    if (nodes.length === 0) return
    const viewport = await control.viewport()
    const state = await control.state()
    for (const node of nodes) {
      const bounds = await control.bounds(node.id)
      const current = (await control.query(label)).find(candidate => candidate.id === node.id)
      // Reject stale IDs/layout snapshots; use only freshly measured native bounds.
      if (!bounds || !current?.bounds || !state.painted.includes(label)) continue
      if (Object.keys(bounds).some(key => bounds[key as keyof Bounds] !== current.bounds![key as keyof Bounds])) continue
      const point = visibleHit(bounds, viewport)
      control.record({ kind: 'target', label, id: node.id, bounds, viewport, point: point ?? null, state })
      if (point) return { id: node.id, bounds, viewport, point, state }
    }
  })
}

export async function nativeClick(control: Control, label: string, expected: (state: State) => boolean) {
  const target = await visibleTarget(control, label)
  assert(!expected(target.state), `${label}: post-click control already true before input`)
  // This is the only delivery path: forward the measured point to native mouse.
  await control.mouseClick(target.point)
  control.record({ kind: 'delivered', label, ...target })
  const after = await waitFor(control, `native state change after ${label}`, async () => {
    const state = await control.state()
    return expected(state) && JSON.stringify(state.text) !== JSON.stringify(target.state.text) ? state : undefined
  })
  control.record({ kind: 'observed', label, id: target.id, point: target.point, before: target.state, after })
  return after
}
