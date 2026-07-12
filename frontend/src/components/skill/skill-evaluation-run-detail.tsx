'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'

import { Skeleton } from '@/components/ui/skeleton'
import { useSkillEvaluationCaseFeedback } from '@/lib/hooks/use-skill-evaluations'
import { formatDisplayNumber, formatDisplayUsd } from '@/lib/utils/display-format'
import type { JsonObject, JsonValue } from '@/lib/types/json'
import type { SkillCaseFeedback, SkillEvaluationRun } from '@/lib/types/skill-evaluation'

import { SkillBenchmarkPanel } from './skill-benchmark-panel'
import { SkillCaseFeedbackControls } from './skill-case-feedback-controls'

type SkillEvaluationRunDetailProps = {
  readonly currentSkillContentHash?: string | null
  readonly isLoading?: boolean
  readonly run: SkillEvaluationRun | null
  /** 케이스 피드백 활성화용 — 없으면 피드백 컨트롤은 렌더하지 않는다. */
  readonly skillId?: string | null
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

function jsonRecord(value: JsonValue): JsonObject | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as JsonObject
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

function runIsStale(run: SkillEvaluationRun, currentSkillContentHash?: string | null): boolean {
  return Boolean(
    currentSkillContentHash &&
    run.skill_content_hash &&
    run.skill_content_hash !== currentSkillContentHash,
  )
}

function usageItems(run: SkillEvaluationRun, t: ReturnType<typeof useTranslations>) {
  const usage = run.usage
  if (!usage?.measured) return []
  const tokens = (usage.tokens_in ?? 0) + (usage.tokens_out ?? 0)
  return [
    t('usageLine.modelCalls', { count: usage.model_calls ?? 0 }),
    t('usageLine.tokens', { count: formatDisplayNumber(tokens) }),
    typeof usage.cost_usd === 'number'
      ? t('usageLine.cost', { value: formatDisplayUsd(usage.cost_usd) })
      : t('usageLine.costUnknown'),
  ]
}

export function SkillEvaluationRunDetail({
  currentSkillContentHash,
  isLoading = false,
  run,
  skillId = null,
}: SkillEvaluationRunDetailProps) {
  const t = useTranslations('skill.detailDialog.evaluation')
  const feedbackEnabled = Boolean(skillId && run && run.status === 'completed')
  const { data: caseFeedback } = useSkillEvaluationCaseFeedback(
    feedbackEnabled ? skillId : null,
    feedbackEnabled ? (run?.evaluation_set_id ?? null) : null,
    feedbackEnabled ? (run?.id ?? null) : null,
  )
  const feedbackByCase = useMemo(() => {
    const map = new Map<number, SkillCaseFeedback>()
    for (const item of caseFeedback ?? []) {
      map.set(item.case_index, item)
    }
    return map
  }, [caseFeedback])

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
  const usageLines = usageItems(run, t)
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

      <SkillBenchmarkPanel run={run} />

      {usageLines.length > 0 ? (
        <div className="mt-4" data-testid="run-usage-line">
          <h4 className="moldy-ui-micro text-muted-foreground">{t('usageLine.title')}</h4>
          <p className="mt-2 text-xs">{usageLines.join(' · ')}</p>
        </div>
      ) : null}

      {run.error_message ? (
        <div className="mt-4 rounded-lg border border-destructive/30 bg-destructive/5 p-3">
          <h4 className="moldy-ui-micro text-destructive">{t('errorTitle')}</h4>
          <p className="mt-2 text-xs text-destructive">{run.error_message}</p>
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
                  // Scope the key to the run so unsaved case-feedback draft
                  // state can't bleed across runs (unnamed cases share the
                  // fallback "Case N" title, which would otherwise collide).
                  key={`${run.id}-${index}`}
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
                  {feedbackEnabled && skillId ? (
                    <SkillCaseFeedbackControls
                      skillId={skillId}
                      evaluationSetId={run.evaluation_set_id}
                      runId={run.id}
                      caseIndex={index}
                      mine={feedbackByCase.get(index) ?? null}
                    />
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
