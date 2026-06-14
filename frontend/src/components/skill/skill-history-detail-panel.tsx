'use client'

import { useTranslations } from 'next-intl'
import type { ReactNode } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { JsonValue } from '@/lib/types/json'
import type { SkillRevisionDetail, SkillRevisionSummary } from '@/lib/types/skill-revision'

type JsonRecord = Readonly<Record<string, JsonValue>>

type SkillHistoryDetailPanelProps = {
  readonly revision: SkillRevisionSummary | null
  readonly detail?: SkillRevisionDetail
  readonly currentRevisionId: string | null
  readonly isLoading: boolean
  readonly rollbackPending: boolean
  readonly onRequestRollback: (revision: SkillRevisionSummary) => void
}

function isJsonRecord(value: JsonValue | null | undefined): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringField(record: JsonRecord, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'string' && value.trim().length > 0) {
      return value
    }
  }
  return null
}

function compactJson(value: JsonValue): string {
  if (value === null) {
    return 'null'
  }
  if (typeof value !== 'object') {
    return String(value)
  }
  return JSON.stringify(value) ?? ''
}

function describeChangelogItem(value: JsonValue): string {
  if (!isJsonRecord(value)) {
    return compactJson(value)
  }
  const title = stringField(value, ['title', 'summary', 'message', 'description'])
  const path = stringField(value, ['path', 'file'])
  const kind = stringField(value, ['kind', 'type', 'operation'])
  return [title, path, kind].filter(Boolean).join(' · ') || compactJson(value)
}

function describeChangedFile(value: JsonValue): string {
  if (!isJsonRecord(value)) {
    return compactJson(value)
  }
  const path = stringField(value, ['path', 'file', 'name'])
  const status = stringField(value, ['status', 'change', 'operation', 'kind'])
  return [path, status].filter(Boolean).join(' · ') || compactJson(value)
}

function describeTarget(name: string, value: JsonValue): string {
  if (!isJsonRecord(value)) {
    return `${name}: ${compactJson(value)}`
  }
  const status = stringField(value, ['status', 'state', 'result'])
  const issueCount = value.issue_count
  const suffix = typeof issueCount === 'number' && issueCount > 0 ? `, ${issueCount} issues` : ''
  return `${name}: ${status ?? compactJson(value)}${suffix}`
}

function compatibilityLines(result: JsonRecord | null | undefined): readonly string[] {
  if (!result) {
    return []
  }
  const targets = result.targets
  if (isJsonRecord(targets)) {
    return Object.entries(targets).map(([name, value]) => describeTarget(name, value))
  }
  const status = stringField(result, ['status', 'state', 'result', 'summary'])
  return [status ?? compactJson(result)]
}

function metricValue(value: JsonValue | undefined): string | null {
  if (typeof value === 'string' && value.trim().length > 0) {
    return value
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  return null
}

function evaluationLines(result: JsonRecord | null | undefined): readonly string[] {
  if (!result) {
    return []
  }
  const metrics = [
    ['status', result.status],
    ['pass_rate', result.pass_rate],
    ['mean_score', result.mean_score],
    ['score', result.score],
    ['run_status', result.run_status],
  ] as const
  const lines = metrics.flatMap(([label, value]) => {
    const formatted = metricValue(value)
    return formatted ? [`${label}: ${formatted}`] : []
  })
  return lines.length > 0 ? lines : [compactJson(result)]
}

function DetailSection({
  title,
  children,
}: {
  readonly title: string
  readonly children: ReactNode
}) {
  return (
    <section className="rounded-lg border border-border/70 p-3">
      <h4 className="text-sm font-semibold">{title}</h4>
      <div className="mt-2 text-xs text-muted-foreground">{children}</div>
    </section>
  )
}

function TextList({ items, empty }: { readonly items: readonly string[]; readonly empty: string }) {
  if (items.length === 0) {
    return <p>{empty}</p>
  }
  return (
    <ul className="space-y-1">
      {items.map((item, index) => (
        <li key={`${item}-${index}`}>{item}</li>
      ))}
    </ul>
  )
}

export function SkillHistoryDetailPanel({
  revision,
  detail,
  currentRevisionId,
  isLoading,
  rollbackPending,
  onRequestRollback,
}: SkillHistoryDetailPanelProps) {
  const t = useTranslations('skill.detailDialog.history')

  if (!revision) {
    return (
      <aside className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
        {t('noDetail')}
      </aside>
    )
  }

  const isCurrent = revision.id === currentRevisionId
  const changelogItems = detail?.changelog_items?.map(describeChangelogItem) ?? []
  const changedFiles = detail?.changed_files?.map(describeChangedFile) ?? []
  const compatibility = compatibilityLines(detail?.compatibility_result)
  const evaluation = evaluationLines(detail?.evaluation_summary)

  return (
    <aside className="space-y-3 rounded-lg border border-border/70 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">
              {t('detailTitle', { number: revision.revision_number })}
            </h3>
            {isCurrent ? (
              <Badge variant="secondary" className="moldy-ui-micro">
                {t('current')}
              </Badge>
            ) : null}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {revision.changelog_summary ?? t(`operation.${revision.operation}`)}
          </p>
        </div>
        <Button
          type="button"
          variant="destructive"
          size="sm"
          onClick={() => onRequestRollback(revision)}
          disabled={isCurrent || rollbackPending}
          aria-label={t('rollbackFor', { number: revision.revision_number })}
        >
          {rollbackPending ? t('rollbackPending') : t('rollback')}
        </Button>
      </div>
      {isCurrent ? (
        <p className="text-xs text-muted-foreground">{t('rollbackCurrentDisabled')}</p>
      ) : null}
      {isLoading ? (
        <Skeleton className="h-32 w-full rounded-lg" />
      ) : (
        <>
          <DetailSection title={t('changelog')}>
            <TextList
              items={
                changelogItems.length > 0
                  ? changelogItems
                  : revision.changelog_summary
                    ? [revision.changelog_summary]
                    : []
              }
              empty={t('noItems')}
            />
          </DetailSection>
          <DetailSection title={t('changedFiles')}>
            <TextList items={changedFiles} empty={t('notRecorded')} />
          </DetailSection>
          <DetailSection title={t('compatibility')}>
            <TextList items={compatibility} empty={t('notRecorded')} />
          </DetailSection>
          <DetailSection title={t('evaluationSnapshot')}>
            <TextList items={evaluation} empty={t('notRecorded')} />
          </DetailSection>
        </>
      )}
    </aside>
  )
}
