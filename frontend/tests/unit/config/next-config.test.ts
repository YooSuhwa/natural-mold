import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

describe('Next config', () => {
  it('uses webpack for production builds until Turbopack build is stable here', () => {
    const packageJson = JSON.parse(readFileSync(resolve(__dirname, '../../../package.json'), 'utf8'))

    expect(packageJson.scripts.build).toBe('pnpm prepare:assets && next build --webpack')
  })

  it('keeps the Turbopack root inside this repository workspace', () => {
    const source = readFileSync(resolve(__dirname, '../../../next.config.ts'), 'utf8')

    expect(source).not.toMatch(/root:\s*['"]\.\.\/\.\.['"]/)
  })

  it('pins the Turbopack root to the workspace root that contains pnpm-linked deps', () => {
    const source = readFileSync(resolve(__dirname, '../../../next.config.ts'), 'utf8')

    expect(source).toContain('fileURLToPath(import.meta.url)')
    expect(source).toContain('const workspaceRoot = dirname(frontendRoot)')
    expect(source).toMatch(/turbopack:\s*{[\s\S]*root:\s*workspaceRoot/)
  })
})
