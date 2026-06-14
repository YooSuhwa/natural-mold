'use client'

import { StatusChip, type StatusChipVariant } from '@/components/shared/status-chip'
import { useTranslations } from 'next-intl'
import type { SkillHealthState, SkillHealthSummary } from '@/lib/types/skill-evaluation'

type SkillHealthBadgeProps = {
  readonly health?: SkillHealthSummary | null
}

function healthVariant(state: SkillHealthState): StatusChipVariant {
  switch (state) {
    case 'ready':
      return 'healthy'
    case 'evaluation_failed':
      return 'unhealthy'
    case 'needs_credentials':
    case 'needs_rerun':
    case 'low_confidence':
    case 'evaluation_running':
      return 'degraded'
    case 'needs_evaluation':
      return 'unknown'
  }
}

export function SkillHealthBadge({ health }: SkillHealthBadgeProps) {
  const t = useTranslations('skill.health')
  if (!health) return null

  return <StatusChip variant={healthVariant(health.state)} label={t(health.state)} />
}
