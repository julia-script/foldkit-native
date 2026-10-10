// Qualification-only, lossless decoding of the native capture's 8-bit RGBA PNG.
// Other encodings fail closed; alpha is read from the image, never synthesized.
import assert from 'node:assert/strict'
import { readFileSync, statSync } from 'node:fs'
import { inflateSync } from 'node:zlib'

const limit = 64 * 1024 * 1024
const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])

function crc(bytes: Uint8Array) {
  let value = 0xffffffff
  for (const byte of bytes) {
    value ^= byte
    for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0)
  }
  return (value ^ 0xffffffff) >>> 0
}

export function decodeCapturePng(file: Buffer) {
  assert(file.length <= limit && file.subarray(0, 8).equals(signature), 'invalid PNG signature/size')
  let offset = 8, width = 0, height = 0, ended = false, dataEnded = false
  const chunks: Buffer[] = []
  while (offset < file.length) {
    assert(file.length - offset >= 12, 'truncated PNG chunk')
    const length = file.readUInt32BE(offset), end = offset + 12 + length
    assert(end <= file.length, 'truncated PNG chunk data')
    const type = file.toString('ascii', offset + 4, offset + 8)
    assert(file.subarray(offset + 4, offset + 8).every(v => v >= 65 && v <= 90 || v >= 97 && v <= 122) && /^[A-Za-z]{2}[A-Z][A-Za-z]$/.test(type), 'invalid PNG chunk type')
    assert.equal(crc(file.subarray(offset + 4, end - 4)), file.readUInt32BE(end - 4), 'PNG CRC mismatch')
    const data = file.subarray(offset + 8, end - 4)
    assert(width > 0 || type === 'IHDR', 'PNG IHDR must be first')
    if (type === 'IHDR') {
      assert(offset === 8 && length === 13, 'invalid/duplicate PNG IHDR')
      width = data.readUInt32BE(0); height = data.readUInt32BE(4)
      assert(width > 0 && height > 0 && width <= 8192 && height <= 8192 && (width * 4 + 1) * height <= limit, 'invalid PNG dimensions')
      assert(data[8] === 8 && data[9] === 6 && data[10] === 0 && data[11] === 0 && data[12] === 0, 'unsupported capture PNG encoding')
    } else if (type === 'IDAT') {
      assert(!dataEnded, 'noncontiguous PNG IDAT')
      chunks.push(data)
    } else if (type === 'IEND') {
      assert(length === 0 && chunks.length > 0 && end === file.length, 'invalid PNG IEND/trailing data')
      ended = true
    } else {
      assert(type[0] === type[0]!.toLowerCase() && !['tRNS', 'acTL', 'fcTL', 'fdAT'].includes(type), 'unsupported PNG chunk')
    }
    if (chunks.length > 0 && type !== 'IDAT') dataEnded = true
    offset = end
  }
  assert(ended, 'missing PNG IEND')
  const compressed = Buffer.concat(chunks), stride = width * 4, expected = (stride + 1) * height
  // node:zlib's typings omit the info:true result supported by pinned Node/Bun.
  const inflated: unknown = inflateSync(compressed, { maxOutputLength: expected, info: true })
  assert(typeof inflated === 'object' && inflated !== null && 'buffer' in inflated && 'engine' in inflated && Buffer.isBuffer(inflated.buffer), 'missing PNG inflate receipt')
  assert(typeof inflated.engine === 'object' && inflated.engine !== null && 'bytesWritten' in inflated.engine, 'missing PNG inflate byte count')
  assert.equal(inflated.engine.bytesWritten, compressed.length, 'trailing compressed PNG data')
  const raw = inflated.buffer
  assert.equal(raw.length, expected, 'invalid PNG scanline length')
  const rgba = Buffer.alloc(width * height * 4)
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)]!
    assert(filter <= 4, 'invalid PNG filter')
    for (let x = 0; x < stride; x++) {
      const at = y * stride + x
      const a = x >= 4 ? rgba[at - 4]! : 0
      const b = y > 0 ? rgba[at - stride]! : 0
      const c = y > 0 && x >= 4 ? rgba[at - stride - 4]! : 0
      const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c)
      const predictor = filter === 0 ? 0 : filter === 1 ? a : filter === 2 ? b : filter === 3 ? Math.floor((a + b) / 2) : pa <= pb && pa <= pc ? a : pb <= pc ? b : c
      rgba[at] = (raw[y * (stride + 1) + 1 + x]! + predictor) & 255
    }
  }
  return { width, height, pixel(x: number, y: number): [number, number, number, number] {
    assert(Number.isInteger(x) && Number.isInteger(y) && x >= 0 && y >= 0 && x < width && y < height, 'PNG pixel outside image')
    const at = (y * width + x) * 4
    return [rgba[at]!, rgba[at + 1]!, rgba[at + 2]!, rgba[at + 3]!]
  } }
}

export function readCapturePng(path: string) {
  assert(statSync(path).size <= limit, 'capture PNG file too large')
  return decodeCapturePng(readFileSync(path))
}
