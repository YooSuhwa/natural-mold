import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import os from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const scriptsDir = dirname(fileURLToPath(import.meta.url))
const frontendRoot = resolve(scriptsDir, '..')
const checkerName = 'check-frontend-architecture.mjs'
const baselineName = 'frontend-architecture-baseline.json'

function runChecker(root, strict = false) {
  return spawnSync(
    process.execPath,
    [join('scripts', checkerName), ...(strict ? ['--strict'] : [])],
    {
      cwd: root,
      encoding: 'utf8',
    },
  )
}

function withFixture({ baseline, sourcePath = 'src/app/page.tsx', source = '' }, run) {
  const root = mkdtempSync(join(os.tmpdir(), 'moldy-frontend-architecture-'))
  try {
    mkdirSync(join(root, dirname(sourcePath)), { recursive: true })
    mkdirSync(join(root, 'scripts'), { recursive: true })
    copyFileSync(join(scriptsDir, checkerName), join(root, 'scripts', checkerName))
    writeFileSync(join(root, 'scripts', baselineName), JSON.stringify(baseline, null, 2))
    writeFileSync(join(root, sourcePath), source)
    run(root)
  } finally {
    rmSync(root, { force: true, recursive: true })
  }
}

const emptyBaseline = {
  schemaVersion: 1,
  issueKeys: [],
  strictBlockerKeys: [],
}

describe('frontend architecture baseline', () => {
  it('Given the reviewed application tree When the normal and strict guards run Then normal accepts 51 identities and strict retains seven blockers', () => {
    const normal = runChecker(frontendRoot)
    const strict = runChecker(frontendRoot, true)

    expect(normal.status).toBe(0)
    expect(normal.stdout).toContain('frontend architecture issues: 51')
    expect(strict.status).toBe(1)
    expect(strict.stdout).toContain('frontend architecture strict blocking issues: 7')
  })

  it('Given a duplicate reviewed identity When the guard reads the tampered baseline Then it rejects the baseline before scanning source', () => {
    withFixture(
      {
        baseline: {
          schemaVersion: 1,
          issueKeys: ['raw-fetch:src/app/page.tsx', 'raw-fetch:src/app/page.tsx'],
          strictBlockerKeys: [],
        },
      },
      (root) => {
        const result = runChecker(root)
        expect(result.status).toBe(1)
        expect(result.stderr).toContain('issueKeys contains duplicates')
      },
    )
  })

  it.each([
    {
      name: 'duplicate strict blocker identities',
      baseline: {
        schemaVersion: 1,
        issueKeys: ['raw-fetch:src/app/page.tsx'],
        strictBlockerKeys: ['raw-fetch:src/app/page.tsx', 'raw-fetch:src/app/page.tsx'],
      },
      message: 'strictBlockerKeys contains duplicates',
    },
    {
      name: 'unsorted strict blocker identities',
      baseline: {
        schemaVersion: 1,
        issueKeys: ['raw-fetch:src/app/page.tsx', 'tabs:src/app/page.tsx'],
        strictBlockerKeys: ['tabs:src/app/page.tsx', 'raw-fetch:src/app/page.tsx'],
      },
      message: 'strictBlockerKeys must be sorted',
    },
    {
      name: 'strict blocker identity outside the reviewed issue set',
      baseline: {
        schemaVersion: 1,
        issueKeys: [],
        strictBlockerKeys: ['tabs:src/app/page.tsx'],
      },
      message: 'strictBlockerKeys must be a subset of issueKeys',
    },
  ])(
    'Given $name When the guard reads the baseline Then it fails closed',
    ({ baseline, message }) => {
      withFixture({ baseline }, (root) => {
        const result = runChecker(root)
        expect(result.status).toBe(1)
        expect(result.stderr).toContain(message)
      })
    },
  )

  it('Given a reviewed identity absent from source When the guard scans the fixture Then it rejects a silently removed baseline entry', () => {
    withFixture(
      {
        baseline: {
          schemaVersion: 1,
          issueKeys: ['raw-fetch:src/app/page.tsx'],
          strictBlockerKeys: [],
        },
      },
      (root) => {
        const result = runChecker(root)
        expect(result.status).toBe(1)
        expect(result.stderr).toContain(
          'frontend architecture baseline issue missing from source: raw-fetch:src/app/page.tsx',
        )
      },
    )
  })

  it('Given an unreviewed product API import When the guard scans the fixture Then it rejects the new forbidden import', () => {
    withFixture(
      {
        baseline: emptyBaseline,
        source: "import { getAgent } from '@/lib/api/agents'\n\nexport const Page = () => null\n",
      },
      (root) => {
        const result = runChecker(root)
        expect(result.status).toBe(1)
        expect(result.stderr).toContain(
          'frontend architecture new issue: direct-api-import:src/app/page.tsx',
        )
      },
    )
  })

  it('Given a UI primitive importing a domain hook When the guard scans the fixture Then it rejects the reverse layer import', () => {
    withFixture(
      {
        baseline: emptyBaseline,
        sourcePath: 'src/components/ui/primitive.tsx',
        source:
          "import { useAgents } from '@/lib/hooks/use-agents'\n\nexport const Primitive = () => null\n",
      },
      (root) => {
        const result = runChecker(root)
        expect(result.status).toBe(1)
        expect(result.stderr).toContain(
          'frontend architecture new issue: ui-domain-hook-import:src/components/ui/primitive.tsx',
        )
      },
    )
  })
})
