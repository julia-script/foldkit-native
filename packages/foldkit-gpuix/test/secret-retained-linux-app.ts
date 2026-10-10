// Linux qualification of retained content, never painted editor text.
// The CLI always mounts the real native renderer. The exported scenario lets
// bounded headless controls challenge its assertions without opening a window.
import assert from 'node:assert/strict'
import type { GpuixRenderer } from '@gpuix/native'
import { connectStdio } from '@gpuix/native/automation'
import { createMutationQueue, type NativeRenderer } from '@gpuix/native/host'
import { mountGpuix } from '../src/index.ts'

export type PrivacyMode = 'default-disabled' | 'opt-out' | 'enabled'
type Surface = NativeRenderer & Pick<GpuixRenderer, 'getAllText' | 'getAutomationTree' | 'getDebugFrameOverlayStats' | 'focusElement' | 'simulateKeystrokes'>
type App = ReturnType<typeof mountGpuix>

export const qualifyRetainedPrivacy = async (
  app: App, native: Surface, mode: PrivacyMode, listenersBefore: number,
  timeoutMs = 15_000,
) => {
  const observations: Array<Record<string, unknown>> = []
  const record = (phase: string, data: Record<string, unknown>) => {
    const row = { phase, ...data }
    observations.push(row)
    console.error(JSON.stringify({ retainedPrivacyObservation: row }))
  }
  let lastFrame = 0
  const frames = () => {
    const count = native.getDebugFrameOverlayStats().frames
    assert(Number.isSafeInteger(count) && count >= lastFrame, 'invalid/regressing native completed-frame counter')
    lastFrame = count
    return count
  }
  const until = async (condition: () => boolean | Promise<boolean>, label: string) => {
    const end = performance.now() + timeoutMs
    while (!(await condition())) {
      assert(performance.now() < end, `${label}: native qualification did not progress`)
      await new Promise(resolve => setTimeout(resolve, 8))
    }
  }
  const advance = async (phase: string) => {
    app.host.flush()
    const before = frames()
    record(`${phase}-baseline`, { before })
    native.applyBatch('[]')
    try { await until(() => frames() > before, phase) }
    catch (error) { record(`${phase}-failed`, { before, lastFrame }); throw error }
    record(phase, { before, after: frames() })
  }
  const echoQueue = createMutationQueue(native)
  const echo = (field: { nativeId: number }, value: string) => {
    assert(field.nativeId !== 0, 'echo must target an attached input')
    // collect_text traverses element.content, not editor props.value. The
    // input's own controlled content also tests tree-value stripping.
    echoQueue.setText(field.nativeId, value)
    echoQueue.flushMutations()
  }
  const makeField = (value: string) => {
    const field = app.document.createElement('input')
    field.setAttribute('autocomplete', 'one-time-code')
    field.value = value
    field.style.width = '280px'; field.style.height = '40px'
    app.document.body.appendChild(field); app.host.flush(); echo(field, value)
    return field
  }
  const ordinary = app.document.createElement('div')
  ordinary.textContent = 'ORDINARY-CONTENT'
  app.document.body.appendChild(ordinary); app.host.flush()
  const rawOrdinary = native.getAllText()
  record('ordinary-native-control', { raw: rawOrdinary })
  assert(rawOrdinary.includes('ORDINARY-CONTENT'), 'empty ordinary retained-text control')
  await advance('ordinary-completed-frame')

  if (mode !== 'enabled') {
    const field = makeField('596274')
    const raw = native.getAllText()
    record(mode, { raw, listenersBefore, listenersAfter: process.stdin.listenerCount('data') })
    assert(raw.includes('596274'), 'empty disabled-mode retained-secret control')
    assert.equal(process.stdin.listenerCount('data'), listenersBefore, 'automation unexpectedly registered stdin')
    await advance('disabled-secret-completed-frame')
    field.remove(); app.host.flush()
    return { qualification: 'linux-retained-content', mode, observations }
  }

  const feed: Array<(chunk: string) => void> = []
  const write = process.stdout.write.bind(process.stdout)
  process.stdout.write = ((chunk: string | Uint8Array) => {
    const text = typeof chunk === 'string' ? chunk : new TextDecoder().decode(chunk)
    if (text.startsWith('data:')) { for (const callback of feed) callback(text); return true }
    return write(chunk)
  }) as typeof process.stdout.write
  let client: Awaited<ReturnType<typeof connectStdio>> | undefined
  try {
    client = await connectStdio({ write: chunk => { process.stdin.emit('data', chunk) }, feed: callback => feed.push(callback), close: async () => {} })
    const text = async () => await client!.call('getAllText', {}) as { text: Array<string> }
    const secretControl = async (phase: string, field: { nativeId: number }, value: string) => {
      const raw = native.getAllText()
      const rawTree = native.getAutomationTree()
      record(`${phase}-raw`, { raw, rawTree, frames: frames() })
      const find = (node: { id: number; text?: string; children?: Array<unknown> }): typeof node | undefined => {
        if (node.id === field.nativeId) return node
        for (const child of node.children ?? []) {
          const found = find(child as typeof node)
          if (found !== undefined) return found
        }
        return undefined
      }
      assert(raw.includes(value), `${phase}: empty raw retained-secret echo`)
      assert.equal(find(JSON.parse(rawTree))?.text, value, `${phase}: missing raw input-content control`)
      const served = await text()
      const tree = await client!.call('getTree', {})
      record(phase, { raw, rawTree: JSON.parse(rawTree), served, tree, frames: frames() })
      assert(!served.text.some(part => part.includes(value)), `${phase}: secret leaked in SSE text`)
      assert(served.text.includes('•'.repeat([...value].length)), `${phase}: missing positive masked response`)
      assert(!JSON.stringify(tree).includes(value), `${phase}: secret leaked in SSE tree`)
    }
    for (const action of ['clear', 'replace', 'remove'] as const) {
      const field = makeField('596274')
      await secretControl(`${action}-initial-secret`, field, '596274')
      if (action === 'clear') {
        field.value = ''; app.host.flush(); echo(field, '')
      } else if (action === 'replace') {
        field.value = '831905'; app.host.flush(); echo(field, '831905')
        await secretControl('replacement-current-secret', field, '831905')
      } else { field.remove(); app.host.flush() }
      await advance(`${action}-completed-frame`)
      const publicText = app.document.createElement('div')
      publicText.textContent = '596274'
      app.document.body.appendChild(publicText); app.host.flush()
      await advance(`${action}-public-reuse-frame`)
      const raw = native.getAllText()
      const served = await text()
      record(`${action}-public-reuse`, { raw, served, frames: frames() })
      assert(raw.includes('596274'), `${action}: empty public reuse control`)
      assert(served.text.includes('596274'), `${action}: obsolete secret did not retire`)
      if (action === 'replace') assert(!served.text.includes('831905'), 'current replacement leaked during public reuse')
      field.remove(); publicText.remove(); app.host.flush()
      await advance(`${action}-cleanup-frame`)
    }

    const field = makeField('')
    let inputEvents = 0
    field.addEventListener('input', () => { inputEvents++ })
    app.host.flush()
    native.focusElement(field.nativeId)
    await advance('native-input-focus-frame')
    native.simulateKeystrokes('9')
    try { await until(() => inputEvents > 0 && field.value === '9', 'native input event/value') }
    catch (error) { record('native-input-failed', { inputEvents, value: field.value, frames: frames() }); throw error }
    app.host.flush(); echo(field, '9')
    await secretControl('native-input-current-secret', field, '9')
    record('native-input-event', { inputEvents, value: field.value, frames: frames() })
    // Multiple successful host submissions before the next JS await. The last
    // value must remain secret while earlier values become public after a draw.
    for (const value of ['FIRST-CAPTURE', 'SECOND-CAPTURE', 'NEWER-SECRET']) {
      field.value = value; app.host.flush(); echo(field, value)
    }
    await secretControl('rapid-current-newer-secret', field, 'NEWER-SECRET')
    const reused = app.document.createElement('div')
    reused.textContent = 'FIRST-CAPTURE'
    app.document.body.appendChild(reused); app.host.flush()
    await advance('rapid-retirement-frame')
    const raw = native.getAllText()
    const served = await text()
    record('rapid-public-reuse-current-protected', { raw, served, frames: frames() })
    assert(raw.includes('FIRST-CAPTURE'), 'empty rapid public reuse control')
    assert(served.text.includes('FIRST-CAPTURE'), 'covered earlier generation did not retire')
    assert(!served.text.includes('NEWER-SECRET'), 'newer/current secret retired prematurely')
    field.remove(); reused.remove(); app.host.flush()
    await advance('final-cleanup-frame')
    return { qualification: 'linux-retained-content', mode, observations }
  } finally {
    try { await client?.close() } finally { process.stdout.write = write }
  }
}

if (import.meta.main) {
  const run = async () => {
    assert(process.platform === 'linux' && process.env['FKN_LINUX_WINDOWS'] === '1' && process.env['WAYLAND_DISPLAY'], 'Linux real-window session required')
    const mode = process.argv[2]
    assert(mode === 'default-disabled' || mode === 'opt-out' || mode === 'enabled', 'unknown privacy mode')
    if (mode === 'opt-out') process.env['FOLDKIT_NATIVE_AUTOMATION'] = '1'
    else delete process.env['FOLDKIT_NATIVE_AUTOMATION']
    const listeners = process.stdin.listenerCount('data')
    const app = mountGpuix({ title: 'linux-retained-privacy', width: 320, height: 180, exitOnClose: false,
      ...(mode === 'default-disabled' ? {} : { automation: mode === 'enabled' }),
    })
    try {
      const result = await qualifyRetainedPrivacy(app, app.renderer as GpuixRenderer, mode, listeners)
      console.log(JSON.stringify(result))
    } finally { await app.close({ force: true }) }
  }
  run().then(() => process.exit(0), error => { console.error(error); process.exit(1) })
}
