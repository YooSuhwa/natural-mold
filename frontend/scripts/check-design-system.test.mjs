import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

import { describe, expect, it } from 'vitest'

const scriptsDir = path.dirname(fileURLToPath(import.meta.url))

describe('design system guard', () => {
  it('fails closed when a product surface introduces zero-tolerance utilities', () => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'moldy-design-system-'))
    try {
      mkdirSync(path.join(root, 'scripts'), { recursive: true })
      mkdirSync(path.join(root, 'src/app'), { recursive: true })
      copyFileSync(
        path.join(scriptsDir, 'check-design-system.mjs'),
        path.join(root, 'scripts/check-design-system.mjs'),
      )
      writeFileSync(
        path.join(root, 'src/app/page.tsx'),
        'export function Page() { return <div className="rounded-3xl shadow-lg bg-[#123456]" /> }\n',
      )

      const result = spawnSync(process.execPath, ['scripts/check-design-system.mjs'], {
        cwd: root,
        encoding: 'utf8',
      })

      expect(result.status).toBe(1)
      expect(result.stderr).toContain('[large-radius-utility]')
      expect(result.stderr).toContain('[shadow-utility]')
      expect(result.stderr).toContain('[raw-hex-utility]')
    } finally {
      rmSync(root, { force: true, recursive: true })
    }
  })
})
