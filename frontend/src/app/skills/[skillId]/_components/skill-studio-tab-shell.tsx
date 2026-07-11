'use client'

import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'
import type { SkillDetailTabSlots } from '@/components/skill/skill-detail-tab-shell'

/**
 * 스킬 탭 컴포넌트(4슬롯 렌더 프롭 계약)를 풀페이지 스튜디오 레이아웃으로
 * 렌더한다 — 다이얼로그의 `renderSkillDetailTabShell`(DialogShell 매핑)과
 * 동일 슬롯, 다른 프레임. overlay 슬롯은 롤백 확인 다이얼로그 등이 실리므로
 * 반드시 렌더한다 (Phase 2 스펙 AD-3).
 */
export function renderSkillStudioTabShell(slots: SkillDetailTabSlots): ReactNode {
  return <SkillStudioTabShell slots={slots} />
}

function SkillStudioTabShell({ slots }: { readonly slots: SkillDetailTabSlots }) {
  return (
    <>
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {slots.sidebar ? (
          <aside
            className={cn(
              'shrink-0 overflow-y-auto border-r border-border/60 p-4',
              slots.sidebarClassName,
            )}
          >
            {slots.sidebar}
          </aside>
        ) : null}
        <div className={cn('min-w-0 flex-1 overflow-y-auto px-6 py-4', slots.bodyClassName)}>
          {slots.body}
        </div>
      </div>
      {slots.footer ? (
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-border/60 px-6 py-3">
          {slots.footer}
        </div>
      ) : null}
      {slots.overlay ?? null}
    </>
  )
}
