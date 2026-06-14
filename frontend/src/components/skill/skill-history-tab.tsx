'use client'

import { useMemo } from 'react'
import { useLocale, useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useSkillRevisions } from '@/lib/hooks/use-skill-revisions'
import type { SkillRevisionSummary } from '@/lib/types/skill-revision'

function sortRevisionsNewestFirst(
  revisions: readonly SkillRevisionSummary[],
): readonly SkillRevisionSummary[] {
  return [...revisions].sort((a, b) => b.revision_number - a.revision_number)
}

function formatRevisionDate(value: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

export function SkillHistoryTab({
  skillId,
  onClose,
}: {
  readonly skillId: string
  readonly onClose: () => void
}) {
  const t = useTranslations('skill.detailDialog.history')
  const common = useTranslations('skill.detailDialog')
  const locale = useLocale()
  const { data: revisions, isLoading } = useSkillRevisions(skillId)
  const items = useMemo(() => sortRevisionsNewestFirst(revisions ?? []), [revisions])
  const currentRevisionId = items[0]?.id ?? null

  return (
    <>
      <DialogShell.Body>
        {isLoading ? (
          <Skeleton className="h-32 w-full rounded-lg" />
        ) : items.length === 0 ? (
          <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            {t('empty')}
          </div>
        ) : (
          <div className="space-y-2">
            {items.map((revision) => (
              <article key={revision.id} className="rounded-lg border border-border/70 p-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-sm font-semibold">
                        {t('revision', { number: revision.revision_number })}
                      </h3>
                      {revision.id === currentRevisionId ? (
                        <Badge variant="secondary" className="moldy-ui-micro">
                          {t('current')}
                        </Badge>
                      ) : null}
                    </div>
                    <p className="mt-1 moldy-ui-micro text-muted-foreground">
                      {t(`operation.${revision.operation}`)} ·{' '}
                      {formatRevisionDate(revision.created_at, locale)}
                    </p>
                  </div>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {revision.changelog_summary ?? revision.operation}
                </p>
                <dl className="mt-3 grid gap-2 sm:grid-cols-2">
                  <div>
                    <dt className="moldy-ui-micro text-muted-foreground">{t('contentHash')}</dt>
                    <dd className="font-mono text-xs">
                      {revision.content_hash?.slice(0, 12) ?? '--------'}
                    </dd>
                  </div>
                  <div>
                    <dt className="moldy-ui-micro text-muted-foreground">{t('fileCount')}</dt>
                    <dd className="text-xs font-medium">
                      {t('files', { count: revision.file_count })}
                    </dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        )}
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {common('close')}
        </Button>
      </DialogShell.Footer>
    </>
  )
}
