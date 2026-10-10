// Real encoded PNG byte controls; no renderer or native execution.
import { expect, test } from 'bun:test'
import { deflateSync } from 'node:zlib'
import { decodeCapturePng } from './png.ts'
import { pixels, type Context } from './contrast.ts'

// Table CRC implementation independent of the decoder's bit-at-a-time CRC.
const table = Array.from({ length: 256 }, (_, index) => {
  let n = index
  for (let bit = 0; bit < 8; bit++) n = n & 1 ? 0xedb88320 ^ (n >>> 1) : n >>> 1
  return n >>> 0
})
function chunk(type: string, data = Buffer.alloc(0)) {
  const tagged = Buffer.concat([Buffer.from(type), data]), result = Buffer.alloc(data.length + 12)
  result.writeUInt32BE(data.length); tagged.copy(result, 4)
  let crc = 0xffffffff
  for (const byte of tagged) crc = table[(crc ^ byte) & 255]! ^ (crc >>> 8)
  result.writeUInt32BE((crc ^ 0xffffffff) >>> 0, result.length - 4)
  return result
}
const signature = Buffer.from('89504e470d0a1a0a', 'hex')
function header(width = 2, height = 5) {
  const data = Buffer.alloc(13)
  data.writeUInt32BE(width); data.writeUInt32BE(height, 4)
  data[8] = 8; data[9] = 6
  return data
}
function encoded(raw: Buffer, ihdr = header(), extras: Buffer[] = []) {
  return Buffer.concat([signature, chunk('IHDR', ihdr), ...extras, chunk('IDAT', deflateSync(raw)), chunk('IEND')])
}
const rows = Array.from({ length: 5 }, (_, y) => Buffer.from([240 - y * 23, 14 + y * 41, 210 - y * 11, [255, 0, 128, 254, 255][y]!, 35 + y * 47, 200 - y * 39, 13 + y * 53, 255]))
function filtered() {
  return Buffer.concat(rows.map((row, y) => Buffer.from([y, ...Array.from(row, (value, x) => {
    const a = x >= 4 ? row[x - 4]! : 0, b = y ? rows[y - 1]![x]! : 0, c = y && x >= 4 ? rows[y - 1]![x - 4]! : 0
    const nearest = [a, b, c].map((n, order) => ({ n, order, distance: Math.abs(a + b - c - n) })).sort((l, r) => l.distance - r.distance || l.order - r.order)[0]!.n
    return (value - [0, a, b, Math.floor((a + b) / 2), nearest][y]!) & 255
  })])))
}

test('all five PNG filters preserve every actual RGBA byte, including zero/partial alpha', () => {
  const image = decodeCapturePng(encoded(filtered()))
  expect([image.width, image.height]).toEqual([2, 5])
  for (let y = 0; y < 5; y++) for (let x = 0; x < 2; x++) expect(Array.from(image.pixel(x, y))).toEqual(Array.from(rows[y]!.subarray(x * 4, x * 4 + 4)))
  for (const [x, y] of [[-1, 0], [2, 0], [0, 5], [0.5, 0], [NaN, 0]]) expect(() => image.pixel(x!, y!)).toThrow('outside')
})

test('multiple contiguous IDAT chunks and ancillary CRC are checked', () => {
  const compressed = deflateSync(filtered())
  const png = Buffer.concat([signature, chunk('IHDR', header()), chunk('tEXt', Buffer.from('note\0fixture')), chunk('IDAT', compressed.subarray(0, 4)), chunk('IDAT'), chunk('IDAT', compressed.subarray(4)), chunk('IEND')])
  expect(Array.from(decodeCapturePng(png).pixel(1, 4))).toEqual(Array.from(rows[4]!.subarray(4)))
  const broken = Buffer.from(png); broken[40] = broken[40]! ^ 1
  expect(() => decodeCapturePng(broken)).toThrow('CRC')
})

test('unsupported encodings/dimensions/chunks fail without manufactured alpha', () => {
  for (const [at, value] of [[8, 16], [9, 2], [9, 3], [9, 4], [10, 1], [11, 1], [12, 1]]) {
    const h = header(); h[at!] = value!
    expect(() => decodeCapturePng(encoded(filtered(), h))).toThrow('encoding')
  }
  for (const [w, h] of [[0, 5], [2, 0], [8193, 1], [8192, 8192]]) expect(() => decodeCapturePng(encoded(filtered(), header(w, h)))).toThrow('dimensions')
  for (const type of ['PLTE', 'ABCD', 'tRNS', 'acTL', 'fcTL', 'fdAT']) expect(() => decodeCapturePng(encoded(filtered(), header(), [chunk(type)]))).toThrow('unsupported')
})

test('bad signature, truncation, CRC, sequence, filter, zlib and exact scanline length reject', () => {
  const png = encoded(filtered())
  for (const end of [0, 7, 8, 20, 32, png.length - 1, png.length - 12]) expect(() => decodeCapturePng(png.subarray(0, end))).toThrow()
  const bad = Buffer.from(png); bad[0] = 0
  expect(() => decodeCapturePng(bad)).toThrow('signature')
  const crc = Buffer.from(png); crc[30] = crc[30]! ^ 1
  expect(() => decodeCapturePng(crc)).toThrow('CRC')
  const invalid = filtered(); invalid[0] = 5
  expect(() => decodeCapturePng(encoded(invalid))).toThrow('filter')
  for (const raw of [filtered().subarray(0, -1), Buffer.concat([filtered(), Buffer.from([0])])]) expect(() => decodeCapturePng(encoded(raw))).toThrow()
  for (const parts of [
    [chunk('IDAT', deflateSync(filtered())), chunk('IHDR', header()), chunk('IEND')],
    [chunk('IHDR', header()), chunk('IHDR', header()), chunk('IDAT', deflateSync(filtered())), chunk('IEND')],
    [chunk('IHDR', header()), chunk('IEND')],
    [chunk('IHDR', header()), chunk('IDAT', deflateSync(filtered())), chunk('tEXt'), chunk('IDAT'), chunk('IEND')],
    [chunk('IHDR', header()), chunk('IDAT', Buffer.from([0, 1, 2])), chunk('IEND')],
    [chunk('IHDR', header()), chunk('IDAT', Buffer.concat([deflateSync(filtered()), Buffer.from([0])])), chunk('IEND')],
    [chunk('IHDR', header()), chunk('IDAT', deflateSync(filtered())), chunk('IEND', Buffer.from([0]))],
  ]) expect(() => decodeCapturePng(Buffer.concat([signature, ...parts]))).toThrow()
  expect(() => decodeCapturePng(Buffer.concat([png, Buffer.from([0])]))).toThrow('trailing')
})

test('actual decoded RGBA passes strict BODY/label checks; zero and partial encoded alpha reject', () => {
  const scene: Context = { pid: 123, count: 0, at: 100, frames: 9, window: { width: 100, height: 100 },
    probe: { pid: 123, windowId: 5, scale: 1, window: { x: 0, y: 0, width: 100, height: 100 }, display: { x: 0, y: 0, width: 1000, height: 1000 } },
    styles: ['html', 'body', '.app', 'p', 'button'].map((selector, i) => ({ selector, id: [0, 1, 4, 2, 3][i]!, text: selector === 'p' ? 'Count: 0' : selector === 'button' ? '+1' : '', color: '#000000', background: ['body', '.app'].includes(selector) ? '#ffffff' : '', ancestors: [] })),
    app: { id: 4, bounds: { x: 24, y: 24, width: 100, height: 40 } },
    regions: [{ label: 'body', id: 1, bounds: { x: 0, y: 0, width: 100, height: 100 } }, { label: 'Count: 0', id: 2, bounds: { x: 10, y: 10, width: 60, height: 20 } }, { label: '+1', id: 3, bounds: { x: 10, y: 40, width: 60, height: 20 } }] }
  const raw = Buffer.alloc(401 * 100, 255)
  for (let y = 0; y < 100; y++) {
    raw[y * 401] = 0
    for (let x = 10; x < 13; x++) if (y >= 10 && y < 20 || y >= 40 && y < 50) raw.fill(0, y * 401 + 1 + x * 4, y * 401 + 1 + x * 4 + 3)
  }
  const image = decodeCapturePng(encoded(raw, header(100, 100)))
  expect(pixels(image, scene).appOverflow.right).toBe(24)
  for (const alpha of [0, 128, 254]) {
    const changed = Buffer.from(raw); changed[4] = alpha
    expect(decodeCapturePng(encoded(changed, header(100, 100))).pixel(0, 0)[3]).toBe(alpha)
    expect(() => pixels(decodeCapturePng(encoded(changed, header(100, 100))), scene)).toThrow('transparent')
  }
  const outside = structuredClone(scene); outside.regions[0]!.bounds.x = 1
  expect(() => pixels(image, outside)).toThrow()
  const scale = structuredClone(scene); scale.probe.scale = 2
  expect(() => pixels(image, scale)).toThrow()
})
