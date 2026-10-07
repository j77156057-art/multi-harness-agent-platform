import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('contrast gate', () => {
  it('every declared token pair passes WCAG AA (>= 4.5:1)', () => {
    // vitest 的 cwd 就是 frontend/（npm test 从这里发出）。
    const root = process.cwd()
    expect(existsSync(join(root, 'scripts', 'contrast.mjs'))).toBe(true)

    const result = spawnSync(process.execPath, [join('scripts', 'contrast.mjs')], {
      cwd: root,
      encoding: 'utf8',
    })

    expect(result.status, result.stdout + result.stderr).toBe(0)
    expect(result.stdout).toContain('≥ 4.5:1')
  })
})
