import type { ReactNode } from 'react'

/**
 * 스킬 탭 컴포넌트의 4슬롯 렌더 프롭 계약 — 유일한 런타임 렌더러는 스튜디오의
 * `renderSkillStudioTabShell`(app/skills/[skillId]/_components)이다. 구
 * DialogShell 렌더러는 상세 다이얼로그와 함께 제거됐다 (Phase 2).
 */
export type SkillDetailTabSlots = {
  readonly body: ReactNode
  readonly bodyClassName?: string
  readonly footer: ReactNode
  readonly overlay?: ReactNode
  readonly sidebar?: ReactNode
  readonly sidebarClassName?: string
}

export type SkillDetailTabRender = (slots: SkillDetailTabSlots) => ReactNode
