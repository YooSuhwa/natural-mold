import { describe, expect, it } from 'vitest'

import {
  deriveHeadState,
  deriveStatusRows,
  hasEvals,
  hasScripts,
  mergeRailFiles,
} from '../skill-builder-rail-model'
import type { SkillDraftBrief } from '@/lib/stores/chat-skill-builder'

function brief(overrides: Partial<SkillDraftBrief> = {}): SkillDraftBrief {
  return {
    session_id: 's1',
    mode: 'create',
    slug: 'notes',
    file_count: 1,
    files: [{ path: 'SKILL.md', size: 100 }],
    changed_count: 1,
    credential_requirement_count: 0,
    ...overrides,
  }
}

describe('deriveHeadState', () => {
  it('검증 전이면 pending', () => {
    expect(deriveHeadState(null).tone).toBe('pending')
    expect(deriveHeadState(undefined).tone).toBe('pending')
  })

  it('error_count > 0 이면 error, warning만 있으면 warn, 둘 다 없으면 pass', () => {
    expect(deriveHeadState({ valid: false, error_count: 2, warning_count: 1 }).tone).toBe('error')
    expect(deriveHeadState({ valid: true, error_count: 0, warning_count: 3 }).tone).toBe('warn')
    expect(deriveHeadState({ valid: true, error_count: 0, warning_count: 0 }).tone).toBe('pass')
  })
})

describe('deriveStatusRows', () => {
  it('검증 전이면 전 행 pending', () => {
    const rows = deriveStatusRows(null)
    expect(rows).toHaveLength(4)
    expect(rows.every((row) => row.tone === 'pending')).toBe(true)
  })

  it('이슈 코드를 행에 매핑한다 — 트리거 경고/시크릿 오류', () => {
    const rows = deriveStatusRows({
      valid: false,
      error_count: 1,
      warning_count: 1,
      issues: [
        {
          code: 'WEAK_TRIGGER_DESCRIPTION',
          severity: 'warning',
          message: 'Description should state concrete trigger conditions.',
          path: 'SKILL.md',
        },
        {
          code: 'SECRET_DETECTED',
          severity: 'error',
          message: 'Potential secret detected by content scanner.',
          path: 'SKILL.md',
        },
      ],
    })
    const byKey = Object.fromEntries(rows.map((row) => [row.key, row]))
    expect(byKey.frontmatter.tone).toBe('pass')
    expect(byKey.moldyMetadata.tone).toBe('pass')
    expect(byKey.trigger.tone).toBe('warn')
    expect(byKey.trigger.detail).toContain('trigger conditions')
    expect(byKey.secrets.tone).toBe('error')
  })

  it('이슈가 없으면 트리거 행은 good, 나머지는 pass', () => {
    const rows = deriveStatusRows({ valid: true, error_count: 0, warning_count: 0, issues: [] })
    const byKey = Object.fromEntries(rows.map((row) => [row.key, row]))
    expect(byKey.trigger.tone).toBe('good')
    expect(byKey.frontmatter.tone).toBe('pass')
    // 미분류 이슈가 없으면 폴백 행도 없다.
    expect(byKey.other).toBeUndefined()
  })

  it('버킷 밖 이슈는 "기타 검사" 폴백 행으로 노출된다 (R3 — 헤드/상세 모순 방지)', () => {
    const rows = deriveStatusRows({
      valid: false,
      error_count: 1,
      warning_count: 1,
      issues: [
        {
          code: 'UNSUPPORTED_SCRIPT_EXTENSION',
          severity: 'error',
          message: 'Only .py scripts are supported.',
          path: 'scripts/run.sh',
        },
        {
          code: 'UNMENTIONED_REFERENCES',
          severity: 'warning',
          message: 'references/x.md is never mentioned.',
          path: 'references/x.md',
        },
        { code: 'SOME_INFO_ONLY', severity: 'info', message: 'ignore me', path: null },
      ],
    })
    const byKey = Object.fromEntries(rows.map((row) => [row.key, row]))
    expect(byKey.other).toBeDefined()
    expect(byKey.other.tone).toBe('error')
    expect(byKey.other.count).toBe(2) // info는 제외
    expect(byKey.other.detail).toContain('Only .py')
    // NETWORK_PROFILE_MISSING/CREDENTIAL_ENV_*는 moldyMetadata 버킷으로 흡수된다.
    const networkRows = deriveStatusRows({
      valid: false,
      error_count: 1,
      warning_count: 0,
      issues: [
        {
          code: 'NETWORK_PROFILE_MISSING',
          severity: 'error',
          message: 'network profile missing',
          path: 'agents/moldy.yaml',
        },
      ],
    })
    const networkByKey = Object.fromEntries(networkRows.map((row) => [row.key, row]))
    expect(networkByKey.moldyMetadata.tone).toBe('error')
    expect(networkByKey.other).toBeUndefined()
  })
})

describe('mergeRailFiles', () => {
  it('라이브 brief가 있으면 우선한다', () => {
    const merged = mergeRailFiles(brief(), [{ path: 'old.md', size: 1, role: 'asset' }])
    expect(merged.map((file) => file.path)).toEqual(['SKILL.md'])
  })

  it('brief가 없거나 비어 있으면 파일 API로 폴백 (진입 직후/improve 시드)', () => {
    expect(
      mergeRailFiles(undefined, [{ path: 'SKILL.md', size: 10, role: 'skill' }]).map(
        (file) => file.path,
      ),
    ).toEqual(['SKILL.md'])
    expect(
      mergeRailFiles(brief({ files: [], file_count: 0 }), [
        { path: 'seeded.md', size: 5, role: 'reference' },
      ]).map((file) => file.path),
    ).toEqual(['seeded.md'])
  })
})

describe('hasScripts / hasEvals', () => {
  it('scripts/ 접두 파일과 evals/evals.json을 감지한다', () => {
    const files = [
      { path: 'SKILL.md', size: 1 },
      { path: 'scripts/run.py', size: 1 },
      { path: 'evals/evals.json', size: 1 },
    ]
    expect(hasScripts(files)).toBe(true)
    expect(hasEvals(files)).toBe(true)
    expect(hasScripts([{ path: 'SKILL.md', size: 1 }])).toBe(false)
    expect(hasEvals([{ path: 'SKILL.md', size: 1 }])).toBe(false)
  })
})
