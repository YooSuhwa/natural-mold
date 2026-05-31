import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

describe('Next config', () => {
  it('uses webpack for production builds until Turbopack build is stable here', () => {
    const packageJson = JSON.parse(readFileSync(resolve(__dirname, '../../../package.json'), 'utf8'))

    expect(packageJson.scripts.build).toBe('next build --webpack')
  })

  it('does not expand the Turbopack root outside the frontend app', () => {
    const source = readFileSync(resolve(__dirname, '../../../next.config.ts'), 'utf8')

    expect(source).not.toMatch(/root:\s*['"]\.\.['"]/)
  })

  it('pins the Turbopack root to the frontend config directory', () => {
    const source = readFileSync(resolve(__dirname, '../../../next.config.ts'), 'utf8')

    expect(source).toContain('fileURLToPath(import.meta.url)')
    expect(source).toMatch(/turbopack:\s*{[\s\S]*root:\s*frontendRoot/)
  })
})
