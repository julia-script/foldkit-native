// Evidence only: tee the original fixture's stdout without changing its
// bytes, spawn options, exit status, assertions, or timeout.
import { openSync, writeSync, closeSync } from 'node:fs'
import { join, resolve } from 'node:path'

const fixture = resolve(process.env.QUALIFICATION_TARGET, 'packages/foldkit-gpuix/test/secret-stale-native-app.ts')
const spawn = Bun.spawn.bind(Bun)
Bun.spawn = (...args) => {
  const child = spawn(...args)
  const command = Array.isArray(args[0]) ? args[0] : args[0]?.cmd
  if (!command?.includes(fixture)) return child
  const fd = openSync(join(process.env.QUALIFICATION_EVIDENCE, `privacy-child-${child.pid}.json`), 'wx')
  const stdout = child.stdout.pipeThrough(new TransformStream({
    transform(chunk, controller) {
      let offset = 0
      while (offset < chunk.byteLength) offset += writeSync(fd, chunk, offset, chunk.byteLength - offset)
      controller.enqueue(chunk)
    },
    flush() { closeSync(fd) },
  }))
  return new Proxy(child, {
    get(target, key) {
      if (key === 'stdout') return stdout
      const value = Reflect.get(target, key, target)
      return typeof value === 'function' ? value.bind(target) : value
    },
  })
}
