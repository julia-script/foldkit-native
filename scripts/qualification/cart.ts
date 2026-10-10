// External acceptance scenario, adapted from the sole qualifier's saved plan.
// Every input goes through the production app's native automation channel.
import assert from 'node:assert/strict'
import { execFileSync, spawn } from 'node:child_process'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { frameViewport, nativeClick, visibleTarget, waitFor, type Control, type State } from './cart-control.ts'

const root = process.env['QUALIFICATION_TARGET']!
const evidence = process.env['QUALIFICATION_EVIDENCE']!
const { connectStdio } = await import(join(root, 'node_modules/@gpuix/native/dist/automation/index.js'))
const { readPng } = await import(join(root, 'test/support/png.ts'))
await mkdir(evidence, { recursive: true })
const data = await mkdtemp(join(tmpdir(), 'qualification-cart-'))
const observations: Array<unknown> = []
const inputs: Array<unknown> = []
const frames: Array<unknown> = []
const child = spawn(process.execPath, [join(root, 'examples/open.ts'), 'shopping-cart'], {
  cwd: root,
  env: { ...process.env, FOLDKIT_NATIVE_DATA_DIR: data, FOLDKIT_NATIVE_AUTOMATION: '1', FOLDKIT_NATIVE_RENDERER: 'gpuix' },
  stdio: ['pipe', 'pipe', 'pipe'],
})
child.stderr.on('data', chunk => process.stderr.write(chunk))
const exited = new Promise<number | null>((done, reject) => {
  child.once('exit', done)
  child.once('error', reject)
})
const pause = (ms = 350) => Bun.sleep(ms)
try {
  const app = await connectStdio({
    write: (chunk: string) => { child.stdin.write(chunk) },
    feed: (listener: (chunk: string) => void) => { child.stdout.on('data', chunk => listener(chunk.toString('utf8'))) },
    close: async () => { child.kill() },
  })
  const texts = async (): Promise<Array<string>> => (await app.call('getAllText', {})).text
  const painted = async (): Promise<Array<string>> => (await app.call('getPaintedText', {})).text
  const control: Control = {
    query: label => app.getByText(label).all(),
    bounds: async id => (await app.call('getBounds', { elementId: id })).bounds,
    viewport: async () => {
      // initialize reports frontend innerHeight (requested 820), not the actual
      // GPUI viewport. Native screenshots use the Metal drawable in DEVICE pixels.
      // Convert with the measured single display's AppKit backing scale; fail if
      // the owned window/display cannot establish this conversion unambiguously.
      const before = JSON.parse(execFileSync(process.env['QUALIFICATION_FRAME_PROBE']!, [String(child.pid)], { encoding: 'utf8', timeout: 5000 }))
      const path = join(evidence, `cart-frame-${frames.length}.png`)
      await app.screenshot({ path })
      const image = readPng(path)
      const after = JSON.parse(execFileSync(process.env['QUALIFICATION_FRAME_PROBE']!, [String(child.pid)], { encoding: 'utf8', timeout: 5000 }))
      assert.deepEqual(before, after, 'native window/display changed during frame measurement')
      // CGWindow bounds include titlebar; never use its outer height as content.
      const viewport = frameViewport(image, after)
      const measurement = { path, pixels: { width: image.width, height: image.height }, viewport, metadata: after }
      frames.push(measurement)
      console.log(JSON.stringify({ kind: 'native-frame', ...measurement }))
      return viewport
    },
    state: async () => ({ text: await texts(), painted: await painted() }),
    mouseClick: point => app.mouse.click(point),
    record: row => { inputs.push(row); console.log(JSON.stringify(row)) },
    now: () => performance.now(),
    pause: () => pause(50),
  }
  const products = (state: State) => state.text.includes('Add to Cart') && state.painted.includes('Add to Cart') && !state.text.includes('Order Summary') && !state.text.includes('Order placed successfully!')
  const confirmation = (state: State) => state.text.includes('Order placed successfully!') && state.painted.includes('Order placed successfully!') && !state.text.includes('Place Order') && !state.text.includes('Add to Cart')
  const checkout = (state: State) => state.text.includes('Order Summary') && state.text.includes('Place Order') && !state.text.includes('Add to Cart') && !state.text.includes('Order placed successfully!')
  const click = (label: string, expected: (state: State) => boolean) => nativeClick(control, label, expected)
  await waitFor(control, 'native products page', async () => {
    const state = await control.state()
    return products(state) ? state : undefined
  }, 10_000)
  const save = async (label: string) => {
    const path = join(evidence, `${label}.png`)
    await app.screenshot({ path })
    const image = readPng(path)
    assert(image.width > 0 && image.height > 0, 'missing native frame')
    const row = { label, text: await texts(), painted: await painted(), tree: await app.call('getTree', {}), width: image.width, height: image.height }
    assert(row.painted.length > 0, 'native painted-text positive control missing')
    if (label.endsWith('-ready')) assert(row.painted.includes('Place Order'))
    if (label.endsWith('-success') || label.endsWith('-retained')) assert(row.painted.includes('Order placed successfully!'))
    observations.push(row)
    console.log(JSON.stringify(row))
  }
  await click('Checkout', state => state.text.includes('Your cart is empty') && state.painted.includes('Your cart is empty') && !state.text.includes('Add to Cart'))
  assert((await texts()).includes('Your cart is empty'))
  await save('empty-checkout')
  await click('Products', products)
  for (let order = 1; order <= 3; order++) {
    await click('Add to Cart', state => state.text.includes('Cart (1)') && state.painted.includes('Cart (1)'))
    await click('Checkout', checkout)
    const before = await texts()
    assert(before.includes('Place Order') && before.includes('Order Summary'))
    assert(!before.includes('Order placed successfully!'))
    assert(!(await painted()).some(text => text.includes('ring once')), 'old instructions survived a new cart')
    // Establish a visible hit before requiring the ready snapshot's painted label.
    await visibleTarget(control, 'Place Order')
    await save(`order-${order}-ready`)
    if (order === 1) {
      await app.getByType('textarea').fill('ring once')
      await waitFor(control, 'native typing painted the instructions', async () => (await painted()).some(text => text.includes('ring once')) ? true : undefined)
      await save('first-instructions')
    }
    await click('Place Order', confirmation)
    const after = await texts()
    assert(after.includes('Order placed successfully!') && !after.includes('Place Order'))
    await save(`order-${order}-success`)
    if (order < 3) {
      await click(order === 1 ? 'Continue Shopping' : 'Products', products)
      await click('Checkout', confirmation)
      assert((await texts()).includes('Order placed successfully!'), 'navigation lost the confirmation')
      await save(`order-${order}-confirmation-retained`)
      await click('Products', products)
    }
  }
} finally {
  child.kill()
  const outcome = await Promise.race([exited.then(code => ({ code })), pause(5000).then(() => undefined)])
  if (outcome === undefined) child.kill('SIGKILL')
  await writeFile(join(evidence, 'cart-observations.json'), JSON.stringify(observations, null, 2))
  await writeFile(join(evidence, 'cart-inputs.json'), JSON.stringify(inputs, null, 2))
  await writeFile(join(evidence, 'cart-frame-measurements.json'), JSON.stringify(frames, null, 2))
  await rm(data, { recursive: true, force: true })
  assert(outcome !== undefined, 'owned Cart child failed to terminate after SIGTERM')
}
