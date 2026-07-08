import type { SkillDraftBrief } from '@/lib/stores/chat-skill-builder'
import type { SkillBuilderFileEntry } from '@/lib/types/skill-builder'

/**
 * 상태 카드 파생 로직 (M7 — skill-studio 목업 차용).
 *
 * 목업의 검증 행(Portable frontmatter / 트리거 명확성 / Moldy 메타 분리 /
 * 런타임 호환 / Credential / 샌드박스 / 평가)을 **실제 검증기 이슈 코드**로
 * 매핑한다. 목업의 "5/5 통과" 고정 카운트는 실데이터(이슈 리스트)와 맞지 않아
 * 통과/주의/오류 3상태로 각색 (CHECKPOINT M7 감사 노트).
 */

export type StatusTone = 'pass' | 'good' | 'warn' | 'error' | 'pending' | 'none'

export interface StatusRow {
  readonly key: 'frontmatter' | 'moldyMetadata' | 'trigger' | 'secrets' | 'other'
  readonly tone: StatusTone
  /** 이슈에서 온 부가 설명 (첫 이슈 메시지). */
  readonly detail: string | null
  /** 'other' 행 전용 — 미분류 이슈 개수 (라벨 보간). */
  readonly count?: number
}

export interface HeadState {
  readonly tone: 'pending' | 'pass' | 'warn' | 'error'
  readonly errorCount: number
  readonly warningCount: number
}

interface ValidationIssue {
  readonly code: string
  readonly severity: string
  readonly message: string
  readonly path?: string | null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function parseIssues(validation: unknown): ValidationIssue[] {
  if (!isRecord(validation) || !Array.isArray(validation.issues)) return []
  const issues: ValidationIssue[] = []
  for (const raw of validation.issues) {
    if (!isRecord(raw)) continue
    const code = typeof raw.code === 'string' ? raw.code : ''
    const severity = typeof raw.severity === 'string' ? raw.severity : 'info'
    const message = typeof raw.message === 'string' ? raw.message : ''
    if (!code) continue
    issues.push({
      code,
      severity,
      message,
      path: typeof raw.path === 'string' ? raw.path : null,
    })
  }
  return issues
}

export function deriveHeadState(validation: unknown): HeadState {
  if (!isRecord(validation)) {
    return { tone: 'pending', errorCount: 0, warningCount: 0 }
  }
  const errorCount = typeof validation.error_count === 'number' ? validation.error_count : 0
  const warningCount = typeof validation.warning_count === 'number' ? validation.warning_count : 0
  if (errorCount > 0) return { tone: 'error', errorCount, warningCount }
  if (warningCount > 0) return { tone: 'warn', errorCount, warningCount }
  return { tone: 'pass', errorCount, warningCount }
}

function rowTone(matched: ValidationIssue[], passTone: StatusTone): StatusTone {
  if (matched.some((issue) => issue.severity === 'error')) return 'error'
  if (matched.some((issue) => issue.severity === 'warning')) return 'warn'
  return passTone
}

function firstMessage(matched: ValidationIssue[]): string | null {
  const significant = matched.find((issue) => issue.severity !== 'info')
  return significant?.message ?? null
}

/** 검증 이슈 코드 → 목업의 체크 행. 검증 전(validation null)은 전부 pending. */
export function deriveStatusRows(validation: unknown): StatusRow[] {
  if (!isRecord(validation)) {
    return [
      { key: 'frontmatter', tone: 'pending', detail: null },
      { key: 'moldyMetadata', tone: 'pending', detail: null },
      { key: 'trigger', tone: 'pending', detail: null },
      { key: 'secrets', tone: 'pending', detail: null },
    ]
  }
  const issues = parseIssues(validation)
  const byPrefix = (prefixes: string[]) =>
    issues.filter((issue) => prefixes.some((prefix) => issue.code.startsWith(prefix)))

  const frontmatter = byPrefix(['SKILL_MD_', 'INVALID_PATH'])
  const moldyMetadata = byPrefix([
    'MOLDY_METADATA',
    'MOLDY_ONLY_FRONTMATTER',
    'CREDENTIAL_REQUIREMENT',
    'CREDENTIAL_ENV_',
    'UNKNOWN_CREDENTIAL_',
    'NETWORK_PROFILE_MISSING',
  ])
  const trigger = byPrefix(['WEAK_TRIGGER_DESCRIPTION', 'SCAFFOLDING_MARKER'])
  const secrets = byPrefix(['SECRET_DETECTED'])
  // 폴백 행(R3): 헤드 pill은 전체 error/warning 카운트를 쓰는데 상세 행이
  // 부분집합만 매핑하면 "오류 1 / 상세 전부 통과" 모순이 생긴다 — 위 버킷에
  // 안 잡힌 유의미(비-info) 이슈는 "기타 검사 N건"으로 노출한다.
  const bucketed = new Set([...frontmatter, ...moldyMetadata, ...trigger, ...secrets])
  const other = issues.filter((issue) => !bucketed.has(issue) && issue.severity !== 'info')

  const rows: StatusRow[] = [
    { key: 'frontmatter', tone: rowTone(frontmatter, 'pass'), detail: firstMessage(frontmatter) },
    {
      key: 'moldyMetadata',
      tone: rowTone(moldyMetadata, 'pass'),
      detail: firstMessage(moldyMetadata),
    },
    // 목업의 "양호"(good) — 트리거 문구는 통과여도 품질 신호라 별도 톤.
    { key: 'trigger', tone: rowTone(trigger, 'good'), detail: firstMessage(trigger) },
    { key: 'secrets', tone: rowTone(secrets, 'pass'), detail: firstMessage(secrets) },
  ]
  if (other.length > 0) {
    rows.push({
      key: 'other',
      tone: rowTone(other, 'pass'),
      detail: firstMessage(other),
      count: other.length,
    })
  }
  return rows
}

export interface RailFileEntry {
  readonly path: string
  readonly size: number
}

/**
 * 레일 파일 목록 — 라이브 brief(런마다 갱신)가 있으면 우선, 없으면 파일 API
 * (진입 직후·improve 시드 표시용). 캡처 13에서 발견한 "진입 직후 빈 레일" 해소.
 */
export function mergeRailFiles(
  brief: SkillDraftBrief | undefined,
  apiFiles: readonly SkillBuilderFileEntry[] | undefined,
): RailFileEntry[] {
  if (brief && brief.files.length > 0) {
    return brief.files.map((file) => ({ path: file.path, size: file.size }))
  }
  return (apiFiles ?? []).map((file) => ({ path: file.path, size: file.size }))
}

export function hasScripts(files: readonly RailFileEntry[]): boolean {
  return files.some((file) => file.path.startsWith('scripts/'))
}

export function hasEvals(files: readonly RailFileEntry[]): boolean {
  return files.some((file) => file.path === 'evals/evals.json')
}
