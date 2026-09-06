import { copyFileSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

import { describe, expect, it } from 'vitest'

const scriptsDir = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(scriptsDir, '..')

describe('JSX accessibility guard', () => {
  it('fails closed when a new accessibility warning is absent from the reviewed baseline', () => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'moldy-jsx-a11y-'))
    try {
      mkdirSync(path.join(root, 'scripts'), { recursive: true })
      mkdirSync(path.join(root, 'src'), { recursive: true })
      copyFileSync(
        path.join(scriptsDir, 'check-jsx-a11y.mjs'),
        path.join(root, 'scripts/check-jsx-a11y.mjs'),
      )
      copyFileSync(
        path.join(frontendRoot, 'eslint.config.mjs'),
        path.join(root, 'eslint.config.mjs'),
      )
      copyFileSync(
        path.join(frontendRoot, 'eslint.a11y.config.mjs'),
        path.join(root, 'eslint.a11y.config.mjs'),
      )
      copyFileSync(path.join(frontendRoot, 'package.json'), path.join(root, 'package.json'))
      symlinkSync(path.join(frontendRoot, 'node_modules'), path.join(root, 'node_modules'), 'dir')
      writeFileSync(path.join(root, 'scripts/jsx-a11y-baseline.json'), '[]\n')
      writeFileSync(
        path.join(root, 'src/fixture.tsx'),
        'export function Fixture() { return <button type="button" /> }\n',
      )

      const result = spawnSync(process.execPath, ['scripts/check-jsx-a11y.mjs'], {
        cwd: root,
        encoding: 'utf8',
      })

      expect(result.status).toBe(1)
      expect(result.stderr).toContain('New JSX a11y warning(s): 1')
      expect(result.stdout).toContain('jsx-a11y/control-has-associated-label')
    } finally {
      rmSync(root, { force: true, recursive: true })
    }
  })
})
