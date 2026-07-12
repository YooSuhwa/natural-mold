/**
 * 스킬 스튜디오 6탭 IA — pathname에서 활성 탭/컨텍스트를 파생하는 순수 헬퍼.
 *
 * 라우트 계약 (Phase 2 스펙 AD-1):
 *   /skills                                 → list
 *   /skills/builder(/[sessionId])           → builder
 *   /skills/[skillId]/{evaluation,versions,source,settings} → 해당 탭
 *   /skills/[skillId]                       → source (서버 redirect와 동일 해석)
 */

export type SkillStudioTab = 'list' | 'builder' | 'evaluation' | 'versions' | 'source' | 'settings'

export const SKILL_STUDIO_TABS: readonly SkillStudioTab[] = [
  'list',
  'builder',
  'evaluation',
  'versions',
  'source',
  'settings',
]

export type SkillScopedStudioTab = Exclude<SkillStudioTab, 'list' | 'builder'>

const SKILL_SCOPED_TABS: ReadonlySet<string> = new Set([
  'evaluation',
  'versions',
  'source',
  'settings',
])

export function isSkillScopedStudioTab(value: string): value is SkillScopedStudioTab {
  return SKILL_SCOPED_TABS.has(value)
}

export type SkillStudioContext = {
  readonly activeTab: SkillStudioTab
  /** 스킬 스코프 라우트의 skillId (빌더/목록에서는 null). */
  readonly skillId: string | null
  /** 빌더 세션 라우트의 sessionId. */
  readonly sessionId: string | null
}

export function deriveSkillStudioContext(pathname: string | null | undefined): SkillStudioContext {
  const segments = (pathname ?? '').split('/').filter(Boolean)
  if (segments[0] !== 'skills' || segments.length === 1) {
    return { activeTab: 'list', skillId: null, sessionId: null }
  }
  if (segments[1] === 'builder') {
    return { activeTab: 'builder', skillId: null, sessionId: segments[2] ?? null }
  }
  const tab = segments[2]
  return {
    activeTab: tab !== undefined && isSkillScopedStudioTab(tab) ? tab : 'source',
    skillId: segments[1],
    sessionId: null,
  }
}

/**
 * 탭 이동 대상 URL. 스킬 스코프 탭은 컨텍스트 스킬이 없으면 null(비활성).
 * 빌더 탭은 컨텍스트 스킬이 있으면 인덱스에 skillId를 넘겨 해당 스킬의
 * 세션 이력/개선 CTA로 스코프한다.
 */
export function skillStudioTabHref(tab: SkillStudioTab, skillId: string | null): string | null {
  if (tab === 'list') return '/skills'
  if (tab === 'builder') {
    return skillId ? `/skills/builder?skillId=${encodeURIComponent(skillId)}` : '/skills/builder'
  }
  if (!skillId) return null
  return `/skills/${encodeURIComponent(skillId)}/${tab}`
}

/** 레거시 `?detailId=&tab=` 딥링크의 탭 값 → 스튜디오 세그먼트 매핑 (M2b redirect). */
export function legacyDetailTabToStudioTab(tab: string | null | undefined): SkillScopedStudioTab {
  switch (tab) {
    case 'evaluation':
      return 'evaluation'
    case 'history':
      return 'versions'
    case 'credentials':
    case 'metadata':
      return 'settings'
    default:
      return 'source'
  }
}
