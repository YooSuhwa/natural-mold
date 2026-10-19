'use client'

import type { ReactNode } from 'react'
import { useTranslations } from 'next-intl'

import type { SkillDetailTabSlots } from '@/components/skill/skill-detail-tab-shell'

import { renderSkillStudioTabShell } from './skill-studio-tab-shell'

function ReadOnlyNotice() {
  const t = useTranslations('common')
  return (
    <p role="note" className="mb-3 text-xs text-muted-foreground">
      {t('readOnly')}
    </p>
  )
}

/**
 * Studio tab for a caller without `can_edit` (plan 13.9 "탭별 can_view / can_edit"): the
 * body stays readable but its controls are disabled, and the footer (save, rollback)
 * and overlay (confirm dialogs) are dropped. The sidebar (file/revision navigation)
 * keeps working.
 */
export function renderSkillReadOnlyTabShell(slots: SkillDetailTabSlots): ReactNode {
  return renderSkillStudioTabShell({
    ...slots,
    body: (
      <>
        <ReadOnlyNotice />
        <fieldset disabled className="contents" data-testid="skill-read-only">
          {slots.body}
        </fieldset>
      </>
    ),
    footer: null,
    overlay: null,
  })
}
