// Explicit synthetic preload integration control. NEVER selected by workflow.
// Its output MUST fail the native-path validator (backing name is Synthetic).
import { mock } from 'bun:test'
import { createFocusableFake } from '../../packages/foldkit-gpuix/test/support.ts'
import { resolve } from 'node:path'
import { NativeElement } from '../../packages/foldkit-gpuix/src/dom.ts'

class Synthetic {
  flush() {}
  drainEvents() { return [] }
  simulateMouseMove() {}
  getAutomationTree() { return '{}' }
  getElementBounds() { return null }
  getWindowSize() { return { width: 1024, height: 653 } }
}
class MockRenderer {
  native = new Synthetic()
  constructor(size: { width: number; height: number }) { Object.assign(this, createFocusableFake(size).renderer) }
  flush() { this.native.flush() }
  dispatchNativeEvents() { this.native.drainEvents() }
  dispatchEvent() {}
  captureScreenshot() {}
  getAutomationTree() { return this.native.getAutomationTree() }
  getElementBounds() { return this.native.getElementBounds() }
  getWindowSize() { return this.native.getWindowSize() }
  nativeSimulateClick() {}
  nativeSimulateMouseDown() {}
  dispatchMouseMove() { this.native.simulateMouseMove() }
  nativeSimulateMouseUp() {}
}
mock.module('@gpuix/native/testing', () => ({ TestRenderer: MockRenderer, hasNativeTestRenderer: true }))
await import('./preload.ts')
const { openMetal } = await import('../../examples/support/harness.ts')
const app = await openMetal('pixel-art')
await app.settle()
const button = Array.from(app.document.querySelectorAll('[role=radio]')).find(element => element.textContent === '32')
if (button === undefined) throw new Error('synthetic grid control absent')
;(button as unknown as NativeElement).click()
await app.settle()
app.renderer.dispatchMouseMove(1, 1, 0)
await app.settle()
await app.close()
await app.settle()
console.log('synthetic observer integration complete', resolve(import.meta.dir))
// The synthetic command owns this exit. The hosted preload NEVER exits Bun
// or stops the original runtime. Its supervisor reports lingering work.
process.exit(0)
