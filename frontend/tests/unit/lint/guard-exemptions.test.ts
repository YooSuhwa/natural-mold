import { describe, expect, it } from 'vitest'

import { findE2eHygieneIssues } from '../../../scripts/check-e2e-hygiene.mjs'
import { findTypeSafetyIssues } from '../../../scripts/check-type-safety.mjs'

// The type-safety guard scans this tests/ tree with a raw-text regex, so the
// suppression directives used as fixtures below are assembled at runtime —
// a literal bare directive in this file would flag the guard on itself.
const EXPECT_ERROR = '@ts-' + 'expect-error'
const TS_IGNORE = '@ts-' + 'ignore'

function suppressionRules(source: string, filePath: string): string[] {
  return findTypeSafetyIssues(source, filePath)
    .filter((issue) => issue.rule === 'ts-suppression-comment')
    .map((issue) => issue.rule)
}

// (the describe title also avoids the literal directive — the guard scans
// this file's raw text, and the exemption under test must not vouch for it)
describe('type-safety guard: test-file expect-error exemption', () => {
  const reasoned = `// ${EXPECT_ERROR} - SSR 환경 시뮬레이션을 위해 window를 제거한다.\nexport const x = 1\n`

  it('allows a reasoned expect-error in *.test.ts and __tests__/ files', () => {
    expect(suppressionRules(reasoned, 'src/lib/chat/__tests__/foo.test.ts')).toEqual([])
    expect(suppressionRules(reasoned, 'tests/unit/foo.spec.tsx')).toEqual([])
    expect(suppressionRules(reasoned, 'src/lib/chat/__tests__/helpers.ts')).toEqual([])
  })

  it('still flags a reasoned expect-error in production files', () => {
    expect(suppressionRules(reasoned, 'src/lib/chat/foo.ts')).toEqual([
      'ts-suppression-comment',
    ])
  })

  it('still flags a bare expect-error even in test files', () => {
    const bare = `// ${EXPECT_ERROR}\nexport const x = 1\n`
    expect(suppressionRules(bare, 'src/lib/chat/__tests__/foo.test.ts')).toEqual([
      'ts-suppression-comment',
    ])
  })

  it('rejects separator-only pseudo-reasons', () => {
    const dashes = `// ${EXPECT_ERROR} ---\nexport const x = 1\n`
    expect(suppressionRules(dashes, 'src/lib/chat/__tests__/foo.test.ts')).toEqual([
      'ts-suppression-comment',
    ])
  })

  it('still flags ts-ignore in test files even with a reason', () => {
    const ignored = `// ${TS_IGNORE} - reasons do not rehabilitate ts-ignore\nexport const x = 1\n`
    expect(suppressionRules(ignored, 'src/lib/chat/__tests__/foo.test.ts')).toEqual([
      'ts-suppression-comment',
    ])
  })
})

describe('e2e-hygiene guard: captures fixed-timeout exemption', () => {
  const timeoutSource = `import { test } from '@playwright/test'
test('tour', async ({ page }) => {
  await page.waitForTimeout(600)
})
`

  it('allows waitForTimeout only under e2e/captures/', () => {
    expect(findE2eHygieneIssues(timeoutSource, 'e2e/captures/tour.spec.ts')).toEqual([])
  })

  it('still flags waitForTimeout outside e2e/captures/', () => {
    const rules = findE2eHygieneIssues(timeoutSource, 'e2e/regular.spec.ts').map(
      (issue) => issue.rule,
    )
    expect(rules).toEqual(['fixed-timeout'])
  })

  it('still flags focused tests inside e2e/captures/', () => {
    const focused = `import { test } from '@playwright/test'
test.only('tour', async ({ page }) => {
  await page.goto('/')
})
`
    const rules = findE2eHygieneIssues(focused, 'e2e/captures/tour.spec.ts').map(
      (issue) => issue.rule,
    )
    expect(rules).toEqual(['focused-test'])
  })
})
