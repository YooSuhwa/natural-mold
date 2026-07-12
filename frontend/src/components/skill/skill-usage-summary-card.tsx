'use client'

/**
 * 스킬 축 usage 30일 요약 (Phase 3 §3.1/§5, D3 — 실측 귀속만).
 * 평가 런의 실제 LLM 토큰/비용 + 채팅 execute_in_skill 실행 횟수.
 */

import { useTranslations } from 'next-intl'

import { Skeleton } from '@/components/ui/skeleton'
import { useSkillUsage } from '@/lib/hooks/use-skill-usage'
import { formatDisplayNumber, formatDisplayUsd } from '@/lib/utils/display-format'

export function SkillUsageSummaryCard({ skillId }: { readonly skillId: string }) {
  const t = useTranslations('skill.detailDialog.evaluation.usage')
  const { data: usage, isLoading } = useSkillUsage(skillId)

  if (isLoading) {
    return <Skeleton className="h-24 w-full rounded-lg" />
  }
  if (!usage) return null

  const stats = [
    {
      key: 'tokens',
      label: t('tokens'),
      value: formatDisplayNumber(usage.tokens_in + usage.tokens_out),
    },
    {
      key: 'cost',
      label: t('cost'),
      value: usage.priced_event_count > 0 ? formatDisplayUsd(usage.cost_usd) : t('costUnknown'),
    },
    {
      key: 'evaluationRuns',
      label: t('evaluationRuns'),
      value: formatDisplayNumber(usage.evaluation_run_count),
    },
    {
      key: 'executions',
      label: t('executions'),
      value: formatDisplayNumber(usage.chat_execution_count),
    },
  ]

  return (
    <section
      className="rounded-lg border border-border/70 p-3"
      data-testid="skill-usage-summary-card"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{t('title')}</h3>
        <span className="moldy-ui-micro text-muted-foreground">
          {t('window', { days: usage.days })}
        </span>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {stats.map((stat) => (
          <div key={stat.key} className="rounded-lg bg-muted/40 p-2">
            <dt className="moldy-ui-micro text-muted-foreground">{stat.label}</dt>
            <dd className="mt-1 text-sm font-semibold tabular-nums">{stat.value}</dd>
          </div>
        ))}
      </dl>
      {usage.unpriced_token_event_count > 0 ? (
        <p className="mt-2 moldy-ui-micro text-muted-foreground">
          {t('unpricedHint', { count: usage.unpriced_token_event_count })}
        </p>
      ) : null}
    </section>
  )
}
