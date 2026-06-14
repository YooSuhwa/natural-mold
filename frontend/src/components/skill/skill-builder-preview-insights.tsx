'use client'

import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { SkillBuilderSession, SkillDraftPackage } from '@/lib/types'
import { PortableCompatibilityPanel } from './portable-compatibility-panel'
import {
  benchmarkView,
  changelogView,
  compatibilityResult,
  compatibilityTargetKeys,
  fileDiffSummary,
  validationIssueViews,
  type BenchmarkView,
  type ChangelogView,
  type ValidationIssueView,
} from './skill-builder-preview-model'

export function ImproveFileSummary({
  session,
  draft,
}: {
  readonly session: SkillBuilderSession | null
  readonly draft: SkillDraftPackage
}) {
  const t = useTranslations('skill.builderDialog')
  const summary = fileDiffSummary(session, draft)
  if (!summary) {
    return null
  }
  return (
    <section className="moldy-muted-panel space-y-2 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">{t('fileDiffTitle')}</h4>
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="outline">
            {t('originalFileCount', { count: summary.originalCount })}
          </Badge>
          <Badge variant="outline">
            {t('proposedFileCount', { count: summary.proposedCount })}
          </Badge>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Badge variant="secondary">{t('addedCount', { count: summary.added })}</Badge>
        <Badge variant="secondary">{t('changedCount', { count: summary.changed })}</Badge>
        <Badge variant="secondary">{t('deletedCount', { count: summary.deleted })}</Badge>
      </div>
      {summary.files.length > 0 ? (
        <ul className="space-y-1 text-xs text-muted-foreground">
          {summary.files.map((file) => (
            <li key={`${file.kind}-${file.path}`}>
              {file.path} · {t(`fileChange.${file.kind}`)}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}

export function BuilderResultPanel({
  session,
  draft,
}: {
  readonly session: SkillBuilderSession | null
  readonly draft: SkillDraftPackage
}) {
  const t = useTranslations('skill.builderDialog')
  const compatibility = compatibilityResult(session, draft)
  const compatibilityTargets = compatibilityTargetKeys(compatibility)
  const validationIssues = validationIssueViews(session, draft)
  const hasValidation = Boolean(session?.validation_result) || validationIssues.length > 0
  const changelog = changelogView(session, draft)
  const benchmark = benchmarkView(session, draft)
  return (
    <div className="grid gap-2">
      <StatusLine active={hasValidation} label={t('validationTitle')} />
      {hasValidation ? <ValidationPanel issues={validationIssues} /> : null}
      <StatusLine active={compatibilityTargets.length > 0} label={t('compatibilityTitle')} />
      {compatibilityTargets.length > 0 ? (
        <PortableCompatibilityPanel result={compatibility} dense />
      ) : null}
      <StatusLine active={Boolean(changelog)} label={t('changelogTitle')} />
      {changelog ? <ChangelogPanel changelog={changelog} /> : null}
      <StatusLine active={Boolean(benchmark)} label={t('evalTitle')} />
      {benchmark ? <BenchmarkPanel benchmark={benchmark} /> : null}
    </div>
  )
}

function StatusLine({ active, label }: { readonly active: boolean; readonly label: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span
        className={cn(
          'inline-block size-2 rounded-sm',
          active ? 'bg-status-success' : 'bg-muted-foreground/30',
        )}
      />
      {label}
    </div>
  )
}

function ValidationPanel({ issues }: { readonly issues: readonly ValidationIssueView[] }) {
  const t = useTranslations('skill.builderDialog')
  if (issues.length === 0) {
    return (
      <p className="moldy-muted-panel p-3 text-xs text-muted-foreground">{t('validationEmpty')}</p>
    )
  }
  return (
    <section className="moldy-muted-panel space-y-2 p-3">
      {(['error', 'warning', 'info'] as const).map((severity) => {
        const group = issues.filter((issue) => issue.severity === severity)
        if (group.length === 0) {
          return null
        }
        return (
          <div key={severity} className="space-y-1">
            <Badge variant={severity === 'error' ? 'destructive' : 'outline'}>
              {t(`validationSeverity.${severity}`)}
            </Badge>
            <ul className="space-y-1 text-xs text-muted-foreground">
              {group.map((issue) => (
                <li key={`${severity}-${issue.message}`}>{issue.message}</li>
              ))}
            </ul>
          </div>
        )
      })}
    </section>
  )
}

function ChangelogPanel({ changelog }: { readonly changelog: ChangelogView }) {
  return (
    <section className="moldy-muted-panel space-y-2 p-3">
      {changelog.summary ? <p className="text-sm font-medium">{changelog.summary}</p> : null}
      {changelog.items.length > 0 ? (
        <ul className="space-y-1 text-xs text-muted-foreground">
          {changelog.items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}

function BenchmarkPanel({ benchmark }: { readonly benchmark: BenchmarkView }) {
  const t = useTranslations('skill.builderDialog')
  const metrics = [
    benchmark.passRate === null
      ? null
      : t('passRateMetric', { value: Math.round(benchmark.passRate * 100) }),
    benchmark.meanScore === null
      ? null
      : t('meanScoreMetric', { value: benchmark.meanScore.toFixed(2) }),
    benchmark.delta === null ? null : t('deltaMetric', { value: signedMetric(benchmark.delta) }),
  ].filter((value): value is string => value !== null)
  return (
    <section className="moldy-muted-panel flex flex-wrap gap-1.5 p-3">
      {metrics.map((metric) => (
        <Badge key={metric} variant="outline">
          {metric}
        </Badge>
      ))}
    </section>
  )
}

function signedMetric(value: number): string {
  const rounded = value.toFixed(2)
  return value > 0 ? `+${rounded}` : rounded
}
