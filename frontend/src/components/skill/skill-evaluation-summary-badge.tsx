'use client'

import { useTranslations } from 'next-intl'

import { StatusChip, type StatusChipVariant } from '@/components/shared/status-chip'
import type {
  SkillEvaluationRunStatus,
  SkillLatestEvaluationSummary,
} from '@/lib/types/skill-evaluation'

type SkillEvaluationSummaryBadgeProps = {
  readonly summary?: SkillLatestEvaluationSummary | null
}

type EvaluationStatus = SkillEvaluationRunStatus | 'missing' | 'stale' | 'partial' | 'passed'

function normalizedRate(value: number): number {
  const percent = value <= 1 ? value * 100 : value
  return Math.max(0, Math.min(100, Math.round(percent)))
}

function statusSupportsPassRate(status: EvaluationStatus): boolean {
  return status === 'completed' || status === 'passed'
}

function evaluationVariant(status: EvaluationStatus, passRate?: number | null): StatusChipVariant {
  if (typeof passRate === 'number' && statusSupportsPassRate(status)) {
    return passRate >= 0.8 ? 'healthy' : 'degraded'
  }

  switch (status) {
    case 'passed':
    case 'completed':
      return 'healthy'
    case 'failed':
    case 'cancelled':
      return 'unhealthy'
    case 'queued':
    case 'running':
    case 'grading':
    case 'partial':
    case 'stale':
      return 'degraded'
    case 'missing':
      return 'unknown'
  }
}

export function SkillEvaluationSummaryBadge({ summary }: SkillEvaluationSummaryBadgeProps) {
  const t = useTranslations('skill.evaluationSummary')
  if (!summary) return null

  const label =
    typeof summary.pass_rate === 'number' && statusSupportsPassRate(summary.status)
      ? t('passRate', { rate: normalizedRate(summary.pass_rate) })
      : t(`status.${summary.status}`)

  return <StatusChip variant={evaluationVariant(summary.status, summary.pass_rate)} label={label} />
}
