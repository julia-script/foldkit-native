// External acceptance scenario, adapted from the sole qualifier's saved plan.
// Every input goes through the production app's native automation channel.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = process.env['QUALIFICATION_TARGET']!
const evidence = process.env['QUALIFICATION_EVIDENCE']!
const { connectStdio } = await import(join(root, 'node_modules/@gpuix/native/dist/automation/index.js'))
const { readPng } = await import(join(root, 'test/support/png.ts'))
await mkdir(evidence, { recursive: true })
const data = await mkdtemp(join(tmpdir(), 'qualification-cart-'))
const observations: Array<unknown> = []
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
  const readyBy = performance.now() + 10_000
  while ((await app.getByText('Add to Cart').count()) === 0) {
    assert(performance.now() < readyBy, 'products did not become ready in 10 seconds')
    await pause(50)
  }
  await pause(600)
  const click = async (label: string) => {
    const node = (await app.getByText(label).all()).find((item: { bounds?: unknown }) => item.bounds !== undefined)
    assert(node?.bounds, `missing painted target: ${label}`)
    const { x, y, width, height } = node.bounds
    assert(width > 0 && height > 0, `empty painted target: ${label}`)
    await app.mouse.click({ x: x + width / 2, y: y + height / 2 })
    await pause()
  }
  const texts = async (): Promise<Array<string>> => (await app.call('getAllText', {})).text
  const painted = async (): Promise<Array<string>> => (await app.call('getPaintedText', {})).text
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
  await click('Checkout')
  assert((await texts()).includes('Your cart is empty'))
  await save('empty-checkout')
  await click('Products')
  for (let order = 1; order <= 3; order++) {
    await click('Add to Cart')
    await click('Checkout')
    const before = await texts()
    assert(before.includes('Place Order') && before.includes('Order Summary'))
    assert(!before.includes('Order placed successfully!'))
    assert(!(await painted()).some(text => text.includes('ring once')), 'old instructions survived a new cart')
    await save(`order-${order}-ready`)
    if (order === 1) {
      await app.getByType('textarea').fill('ring once')
      await pause()
      assert((await painted()).some(text => text.includes('ring once')), 'native typing did not paint the instructions')
      await save('first-instructions')
    }
    await click('Place Order')
    const after = await texts()
    assert(after.includes('Order placed successfully!') && !after.includes('Place Order'))
    await save(`order-${order}-success`)
    if (order < 3) {
      await click(order === 1 ? 'Continue Shopping' : 'Products')
      await click('Checkout')
      assert((await texts()).includes('Order placed successfully!'), 'navigation lost the confirmation')
      await save(`order-${order}-confirmation-retained`)
      await click('Products')
    }
  }
} finally {
  child.kill()
  const outcome = await Promise.race([exited.then(code => ({ code })), pause(5000).then(() => undefined)])
  if (outcome === undefined) child.kill('SIGKILL')
  await writeFile(join(evidence, 'cart-observations.json'), JSON.stringify(observations, null, 2))
  await rm(data, { recursive: true, force: true })
  assert(outcome !== undefined, 'owned Cart child failed to terminate after SIGTERM')
}
