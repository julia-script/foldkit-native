// Loaded AFTER the normal DOM preload, before the unchanged native test.
import { mock } from 'bun:test'
import { writeFileSync } from 'node:fs'
import { loadavg, freemem, totalmem } from 'node:os'
import { resolve } from 'node:path'
import { createObserver } from './observer.ts'

const file = process.env['PIXEL_DRAG_PROFILE_OUTPUT']
if (!file || process.platform !== 'darwin' || process.env['FOLDKIT_NATIVE_RENDERER'] !== 'gpuix') throw new Error('native profile requires Darwin, explicit gpuix, and an output file')
const resource = () => ({ cpu: process.cpuUsage(), memory: process.memoryUsage(), usage: process.resourceUsage(), load: loadavg(), freeMemory: freemem(), totalMemory: totalmem() })
const before = resource()
const observer = createObserver(() => save('close'))
let instances = 0
let realBacking = false
const testing = await import('@gpuix/native/testing')
if (!testing.hasNativeTestRenderer) throw new Error('real native TestRenderer unavailable')
const Constructor = testing.TestRenderer
const ObservedRenderer = new Proxy(Constructor, {
  construct(target, args) {
    // Reflect constructs the ORIGINAL class, with its original prototype and
    // native constructor; this neither subclasses nor substitutes a renderer.
    const instance = Reflect.construct(target, args, target)
    instances++
    realBacking = instance instanceof Constructor && Object.getPrototypeOf(instance) === Constructor.prototype && Reflect.get(instance, 'native')?.constructor?.name === 'TestGpuixRenderer'
    observer.requested(args[0])
    observer.instrumentRenderer(instance)
    return instance
  },
})
mock.module('@gpuix/native/testing', () => ({ ...testing, TestRenderer: ObservedRenderer }))

const supportPath = resolve(import.meta.dir, '../../packages/foldkit-gpuix/test/support.ts')
const support = await import(supportPath)
const open = observer.observe('open.lower', support.openMetal, { after: app => observer.attach(app) })
mock.module(supportPath, () => ({ ...support, openMetal: open }))

// Exact function arguments/results/throws retained. Update and VDOM creation
// are synchronous; deferred Effect command execution is NOT this metric.
const updatePath = resolve(import.meta.dir, '../../examples/pixel-art/update.ts')
const update = await import(updatePath)
mock.module(updatePath, () => ({ ...update, update: observer.observe('app.update', update.update) }))
const viewPath = resolve(import.meta.dir, '../../examples/pixel-art/view/index.ts')
const view = await import(viewPath)
mock.module(viewPath, () => ({ ...view, view: observer.observe('app.view', view.view) }))
const harnessPath = resolve(import.meta.dir, '../../examples/support/harness.ts')
const harness = await import(harnessPath)
mock.module(harnessPath, () => ({ ...harness, openMetal: observer.observe('open.example', harness.openMetal) }))
observer.instrumentTimers(globalThis)
const save = (stage: string, exitCode?: number) => writeFileSync(file, JSON.stringify({
  schema: 1, stage, exitCode, renderer: 'real-native', instances, realBacking,
  pid: process.pid, platform: process.platform, arch: process.arch, bun: Bun.version,
  sourceHead: process.env['PIXEL_DRAG_PROFILE_HEAD'], before, after: resource(),
  timing: 'inclusive; nested metrics overlap; async observer returns original promises',
  ...observer.snapshot(),
}, null, 2) + '\n')
process.on('beforeExit', () => save('beforeExit'))
process.on('exit', code => save('exit', code))
// Close snapshot survives a child stuck after Bun's timeout; only exit output
// with balanced calls can satisfy the completeness validator.
