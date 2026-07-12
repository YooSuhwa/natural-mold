'use client'

/**
 * 버전별 통과율 추이 (Phase 3 §6) — completed 평가 런을
 * (skill_version, content_hash)로 묶은 실측 집계를 바 리스트로 보여준다.
 */

import { useTranslations } from 'next-intl'

import { Skeleton } from '@/components/ui/skeleton'
import { useSkillEvaluationVersionStats } from '@/lib/hooks/use-skill-evaluations'

import { SkillMetricBarList, type SkillMetricBarRow } from './skill-metric-bar-list'

function ratePercent(value: number): string {
  const percent = value <= 1 ? value * 100 : value
  return `${Math.max(0, Math.min(100, Math.round(percent)))}%`
}

export function SkillVersionPassRatePanel({ skillId }: { readonly skillId: string }) {
  const t = useTranslations('skill.detailDialog.evaluation.versionStats')
  const { data: stats, isLoading } = useSkillEvaluationVersionStats(skillId)

  if (isLoading) {
    return <Skeleton className="h-24 w-full rounded-lg" />
  }
  const items = stats ?? []
  if (items.length === 0) return null

  const rows: SkillMetricBarRow[] = items
    .filter((item) => item.latest_pass_rate !== null && item.latest_pass_rate !== undefined)
    .map((item, index) => {
      const rate = item.latest_pass_rate ?? 0
      const version = item.skill_version ?? item.content_hash?.slice(0, 8) ?? `#${index + 1}`
      return {
        key: `${item.skill_version ?? 'none'}-${item.content_hash ?? index}`,
        label: version,
        ratio: rate,
        display: ratePercent(rate),
        tone: 'primary' as const,
        meta: t('runCount', { count: item.run_count }),
      }
    })
  if (rows.length === 0) return null

  return (
    <section
      className="rounded-lg border border-border/70 p-3"
      data-testid="skill-version-pass-rate-panel"
    >
      <h3 className="text-sm font-semibold">{t('title')}</h3>
      <p className="mt-1 moldy-ui-micro text-muted-foreground">{t('hint')}</p>
      <div className="mt-3">
        <SkillMetricBarList rows={rows} testId="skill-version-pass-rate-bars" />
      </div>
    </section>
  )
}
