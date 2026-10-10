// Dedicated Linux native retained-content qualification. No painted/selected
// editor-text guarantee. Each mode owns one child; logs survive assertions.
import { expect, test } from 'bun:test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { WINDOWS } from '../../../test/support/windows.ts'

const SESSION = process.platform === 'linux' && WINDOWS && process.env['FKN_SWAYSOCK'] !== undefined
for (const mode of ['default-disabled', 'opt-out', 'enabled'] as const) {
  test.skipIf(!SESSION)(`linux retained privacy: ${mode}`, async () => {
    const child = Bun.spawn([process.execPath, `${import.meta.dir}/secret-retained-linux-app.ts`, mode], {
      stdin: 'pipe', stdout: 'pipe', stderr: 'pipe', env: { ...process.env },
    })
    const stdout = new Response(child.stdout).text()
    const stderr = new Response(child.stderr).text()
    const timer = setTimeout(() => child.kill(), 120_000)
    try {
      const exit = await child.exited
      const [out, errors] = await Promise.all([stdout, stderr])
      const evidence = process.env['LINUX_PRIVACY_EVIDENCE']
      if (evidence !== undefined) {
        mkdirSync(evidence, { recursive: true })
        writeFileSync(join(evidence, `${mode}.stdout`), out)
        writeFileSync(join(evidence, `${mode}.stderr`), errors)
        writeFileSync(join(evidence, `${mode}.exit`), String(exit))
        writeFileSync(join(evidence, `${mode}.child`), String(child.pid))
      }
      if (exit !== 0) throw new Error(`Linux retained privacy ${mode} failed (${exit}): ${errors}\n${out}`)
      const result = JSON.parse(out.trim()) as { qualification: string; mode: string; observations: Array<{ phase: string; before?: number; after?: number }> }
      expect(result.qualification).toBe('linux-retained-content')
      expect(result.mode).toBe(mode)
      expect(result.observations.map(row => row.phase)).toContain('ordinary-native-control')
      const progresses = result.observations.filter(row => row.before !== undefined && row.after !== undefined)
      expect(progresses.length).toBeGreaterThan(0)
      for (const row of progresses) expect(row.after!).toBeGreaterThan(row.before!)
      if (mode === 'enabled') {
        for (const phase of ['clear-public-reuse', 'replace-public-reuse', 'remove-public-reuse', 'native-input-event', 'rapid-public-reuse-current-protected']) {
          expect(result.observations.map(row => row.phase)).toContain(phase)
        }
      }
    } finally { clearTimeout(timer); child.kill() }
  }, 130_000)
}
