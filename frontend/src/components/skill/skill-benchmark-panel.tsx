'use client'

/**
 * 실측 A/B 벤치마크 비교 (Phase 3 §4) — run.benchmark의 with/without 지표를
 * 바 차트로 비교한다. llm-2(measured) 런은 "실측" 배지, 레거시 런은 "추정"
 * 라벨로 정직하게 구분한다 (가짜 데이터 금지 원칙).
 */

import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { formatDisplayNumber } from '@/lib/utils/display-format'
import type { JsonValue } from '@/lib/types/json'
import type { SkillEvaluationRun } from '@/lib/types/skill-evaluation'

import { SkillMetricBarList, type SkillMetricBarRow } from './skill-metric-bar-list'

function numberOrNull(value: JsonValue | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function ratePercent(value: number): string {
  const percent = value <= 1 && value >= -1 ? value * 100 : value
  return `${Math.max(0, Math.min(100, Math.round(percent)))}%`
}

/** Signed percent for deltas — a regression (negative delta) must stay negative. */
function signedRatePercent(value: number): string {
  const percent = Math.round(value <= 1 && value >= -1 ? value * 100 : value)
  const clamped = Math.max(-100, Math.min(100, percent))
  return `${clamped > 0 ? '+' : ''}${clamped}%`
}

export function SkillBenchmarkPanel({ run }: { readonly run: SkillEvaluationRun }) {
  const t = useTranslations('skill.detailDialog.evaluation.abBenchmark')
  const benchmark = run.benchmark ?? null
  if (!benchmark) return null

  const measured = benchmark['measured'] === true
  const baselineSkipped = benchmark['baseline_skipped'] === true
  const withPassRate = numberOrNull(benchmark['with_skill_pass_rate'])
  const withoutPassRate = numberOrNull(benchmark['without_skill_pass_rate'])
  const passRateDelta = numberOrNull(benchmark['pass_rate_delta'])
  const tokenDelta = numberOrNull(benchmark['token_delta'])
  const durationDelta = numberOrNull(benchmark['duration_delta_ms'])

  if (withPassRate === null) return null

  const rows: SkillMetricBarRow[] = [
    {
      key: 'with',
      label: t('withSkill'),
      ratio: withPassRate,
      display: ratePercent(withPassRate),
      tone: 'primary',
    },
  ]
  if (withoutPassRate !== null) {
    rows.push({
      key: 'without',
      label: t('withoutSkill'),
      ratio: withoutPassRate,
      display: ratePercent(withoutPassRate),
      tone: 'baseline',
    })
  }

  const deltaItems = [
    passRateDelta === null ? null : t('passRateDelta', { rate: signedRatePercent(passRateDelta) }),
    tokenDelta === null
      ? null
      : t('tokenDelta', { count: formatDisplayNumber(Math.round(tokenDelta)) }),
    durationDelta === null
      ? null
      : t('durationDelta', { count: formatDisplayNumber(Math.round(durationDelta)) }),
  ].filter((item): item is string => item !== null)

  return (
    <div className="mt-4" data-testid="skill-benchmark-panel">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="moldy-ui-micro text-muted-foreground">{t('title')}</h4>
        {measured ? (
          <Badge variant="secondary" className="moldy-ui-micro" data-testid="benchmark-measured">
            {t('measured')}
          </Badge>
        ) : (
          <Badge variant="outline" className="moldy-ui-micro" data-testid="benchmark-estimated">
            {t('estimated')}
          </Badge>
        )}
        {baselineSkipped ? (
          <span className="moldy-ui-micro text-muted-foreground">{t('baselineSkipped')}</span>
        ) : null}
      </div>
      <div className="mt-2">
        <SkillMetricBarList rows={rows} testId="skill-benchmark-bars" />
      </div>
      {deltaItems.length > 0 ? (
        <ul className="mt-2 space-y-1">
          {deltaItems.map((item) => (
            <li key={item} className="text-xs text-muted-foreground">
              {item}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
