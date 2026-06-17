import { existsSync, readdirSync } from 'node:fs'
import path from 'node:path'

import { describe, expect, it } from 'vitest'

const APP_DIR = path.join(process.cwd(), 'src/app')
const ROUTE_FILE_NAMES = new Set(['page.ts', 'page.tsx'])
const FORBIDDEN_SKILL_ROUTES = new Set([
  '/skill-credentials',
  '/skill-evaluation',
  '/skill-evaluations',
  '/skill-history',
  '/skill-rollback',
  '/skill-compatibility',
  '/skills/credentials',
  '/skills/evaluation',
  '/skills/evaluations',
  '/skills/history',
  '/skills/rollback',
  '/skills/compatibility',
])

function collectPageRoutes(dir: string, segments: readonly string[] = []): readonly string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      const nextSegments = entry.name.startsWith('(') ? segments : [...segments, entry.name]
      return collectPageRoutes(entryPath, nextSegments)
    }
    if (entry.isFile() && ROUTE_FILE_NAMES.has(entry.name)) {
      return [`/${segments.join('/')}`]
    }
    return []
  })
}

describe('Skill routes', () => {
  it('keeps advanced skill surfaces inside the single /skills detail route', () => {
    expect(existsSync(APP_DIR)).toBe(true)

    const routes = collectPageRoutes(APP_DIR)

    expect(routes).toContain('/skills')
    expect(routes.filter((route) => FORBIDDEN_SKILL_ROUTES.has(route))).toEqual([])
  })
})
