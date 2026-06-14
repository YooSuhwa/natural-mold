'use client'

import { useTranslations } from 'next-intl'

import { Skeleton } from '@/components/ui/skeleton'
import type { JsonValue } from '@/lib/types/json'
import type { SkillEvaluationRun } from '@/lib/types/skill-evaluation'

type SkillEvaluationRunDetailProps = {
  readonly currentSkillContentHash?: string | null
  readonly isLoading?: boolean
  readonly run: SkillEvaluationRun | null
}

function normalizedRate(value: number): number {
  const percent = value <= 1 ? value * 100 : value
  return Math.max(0, Math.min(100, Math.round(percent)))
}

function fixedSecondsFromMs(value: number): string {
  const seconds = value / 1000
  return seconds.toFixed(Number.isInteger(seconds) ? 0 : 1)
}

function numberValue(record: Readonly<Record<string, JsonValue>> | null | undefined, key: string) {
  const value = record?.[key]
  return typeof value === 'number' ? value : null
}

function textValue(value: JsonValue | undefined): string | null {
  if (typeof value === 'string' && value.trim().length > 0) return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return null
}

function jsonPreview(value: JsonValue | undefined): string | null {
  const text = textValue(value)
  if (text) return text
  if (value === undefined || value === null) return null
  return JSON.stringify(value)
}

function jsonRecord(value: JsonValue): Readonly<Record<string, JsonValue>> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value
}

function metricItems(run: SkillEvaluationRun, t: ReturnType<typeof useTranslations>) {
  const passRate = numberValue(run.summary, 'pass_rate')
  const triggerAccuracy = numberValue(run.summary, 'trigger_accuracy')
  const averageDurationMs = numberValue(run.summary, 'average_duration_ms')
  const tokenDelta = numberValue(run.summary, 'token_delta')

  return [
    passRate === null ? null : t('metric.passRate', { rate: normalizedRate(passRate) }),
    triggerAccuracy === null
      ? null
      : t('metric.triggerAccuracy', { rate: normalizedRate(triggerAccuracy) }),
    averageDurationMs === null
      ? null
      : t('metric.averageDuration', { seconds: fixedSecondsFromMs(averageDurationMs) }),
    tokenDelta === null ? null : t('metric.tokenDelta', { count: tokenDelta }),
  ].filter((item): item is string => item !== null)
}

function benchmarkItems(run: SkillEvaluationRun, t: ReturnType<typeof useTranslations>) {
  const durationDeltaMs = numberValue(run.benchmark, 'duration_delta_ms')
  const tokenDelta = numberValue(run.benchmark, 'token_delta')
  const qualityDelta = numberValue(run.benchmark, 'quality_delta')

  return [
    durationDeltaMs === null ? null : t('benchmark.durationDelta', { value: durationDeltaMs }),
    tokenDelta === null ? null : t('benchmark.tokenDelta', { count: tokenDelta }),
    qualityDelta === null
      ? null
      : t('benchmark.qualityDelta', { rate: normalizedRate(qualityDelta) }),
  ].filter((item): item is string => item !== null)
}

function runIsStale(run: SkillEvaluationRun, currentSkillContentHash?: string | null): boolean {
  return Boolean(
    currentSkillContentHash &&
    run.skill_content_hash &&
    run.skill_content_hash !== currentSkillContentHash,
  )
}

export function SkillEvaluationRunDetail({
  currentSkillContentHash,
  isLoading = false,
  run,
}: SkillEvaluationRunDetailProps) {
  const t = useTranslations('skill.detailDialog.evaluation')

  if (isLoading) {
    return <Skeleton className="h-48 w-full rounded-lg" />
  }

  if (!run) {
    return (
      <section className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
        {t('noRunDetail')}
      </section>
    )
  }

  const metrics = metricItems(run, t)
  const benchmarks = benchmarkItems(run, t)
  const caseResults = run.case_results ?? []

  return (
    <section className="rounded-lg border border-border/70 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{t('detailTitle')}</h3>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{run.id}</p>
        </div>
        <span className="moldy-ui-micro text-muted-foreground">
          {runIsStale(run, currentSkillContentHash) ? t('staleRun') : t(`status.${run.status}`)}
        </span>
      </div>

      {metrics.length > 0 ? (
        <dl className="mt-4 grid gap-2 sm:grid-cols-2">
          {metrics.map((metric) => (
            <div key={metric} className="rounded-lg bg-muted/40 p-2 text-xs font-medium">
              {metric}
            </div>
          ))}
        </dl>
      ) : null}

      {benchmarks.length > 0 ? (
        <div className="mt-4">
          <h4 className="moldy-ui-micro text-muted-foreground">{t('benchmarkTitle')}</h4>
          <ul className="mt-2 space-y-1">
            {benchmarks.map((benchmark) => (
              <li key={benchmark} className="text-xs">
                {benchmark}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-4">
        <h4 className="moldy-ui-micro text-muted-foreground">{t('caseResultsTitle')}</h4>
        {caseResults.length === 0 ? (
          <p className="mt-2 text-xs text-muted-foreground">{t('noCaseResults')}</p>
        ) : (
          <div className="mt-2 space-y-2">
            {caseResults.map((item, index) => {
              const record = jsonRecord(item)
              const title = textValue(record?.name) ?? t('caseTitle', { number: index + 1 })
              const status = textValue(record?.status)
              const feedback = textValue(record?.grader_feedback) ?? textValue(record?.feedback)
              const evidence = jsonPreview(record?.evidence)
              return (
                <article
                  key={`${title}-${index}`}
                  className="rounded-lg border border-border/60 p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h5 className="text-xs font-semibold">{title}</h5>
                    {status ? (
                      <span className="moldy-ui-micro text-muted-foreground">{status}</span>
                    ) : null}
                  </div>
                  {feedback ? <p className="mt-2 text-xs">{feedback}</p> : null}
                  {evidence ? (
                    <p className="mt-1 line-clamp-3 text-xs text-muted-foreground">{evidence}</p>
                  ) : null}
                </article>
              )
            })}
          </div>
        )}
      </div>
    </section>
  )
}
