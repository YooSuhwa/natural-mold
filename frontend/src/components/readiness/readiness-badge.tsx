'use client'

import { useTranslations } from 'next-intl'

import { StatusChip } from '@/components/shared/status-chip'
import type { ReadinessResult, ReadinessSummary } from '@/lib/types/readiness'

/** Blocked reasons in the order the badge names them (12.2 "blocked"). */
const BLOCKING: readonly ReadinessResult[] = ['no_permission', 'owner_must_connect', 'unavailable']

function count(summary: ReadinessSummary, result: ReadinessResult): number {
  return summary.counts[result] ?? 0
}

/** S21 badge from the 12.2 overall state: ready shows nothing (plan S21 "배지 규칙"). */
export function ReadinessBadge({ summary }: { readonly summary: ReadinessSummary | null }) {
  const t = useTranslations('readiness.badge')
  if (!summary || summary.overall === 'ready') return null
  if (summary.overall === 'needs_connection') {
    const label = t('needsConnection', { count: count(summary, 'connect_by_me') })
    return <StatusChip variant="needs_connection" label={label} />
  }
  if (summary.overall === 'limited') {
    const excluded = Object.entries(summary.counts)
      .filter(([result]) => result !== 'ready')
      .reduce((total, [, n]) => total + (n ?? 0), 0)
    return <StatusChip variant="limited" label={t('limited', { count: excluded })} />
  }
  const reason = BLOCKING.find((result) => count(summary, result) > 0) ?? 'unavailable'
  return <StatusChip variant="blocked" label={t('blocked', { reason: t(`reason.${reason}`) })} />
}
