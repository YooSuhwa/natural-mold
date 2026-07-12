'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'

import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { SkillHistoryDetailPanel } from './skill-history-detail-panel'
import { SkillRevisionDiffCard } from './skill-revision-diff-card'
import {
  useRollbackSkillRevision,
  useSkillRevision,
  useSkillRevisions,
} from '@/lib/hooks/use-skill-revisions'
import { useSkillEvaluationVersionStats } from '@/lib/hooks/use-skill-evaluations'
import { formatDisplayDateTime } from '@/lib/utils/display-format'
import type { SkillRevisionSummary } from '@/lib/types/skill-revision'
import type { SkillDetailTabRender } from './skill-detail-tab-shell'

function sortRevisionsNewestFirst(
  revisions: readonly SkillRevisionSummary[],
): readonly SkillRevisionSummary[] {
  return [...revisions].sort((a, b) => b.revision_number - a.revision_number)
}

export function SkillHistoryTab({
  children,
  skillId,
}: {
  readonly children: SkillDetailTabRender
  readonly skillId: string
}) {
  const t = useTranslations('skill.detailDialog.history')
  const [selectedRevisionId, setSelectedRevisionId] = useState<string | null>(null)
  const [rollbackRevision, setRollbackRevision] = useState<SkillRevisionSummary | null>(null)
  const { data: revisions, isLoading } = useSkillRevisions(skillId)
  const { data: versionStats } = useSkillEvaluationVersionStats(skillId)
  const passRateByContentHash = useMemo(() => {
    const map = new Map<string, number>()
    for (const stat of versionStats ?? []) {
      if (stat.content_hash && typeof stat.latest_pass_rate === 'number') {
        map.set(stat.content_hash, stat.latest_pass_rate)
      }
    }
    return map
  }, [versionStats])
  const items = useMemo(() => sortRevisionsNewestFirst(revisions ?? []), [revisions])
  const currentRevisionId = items[0]?.id ?? null
  const resolvedSelectedRevisionId = selectedRevisionId ?? currentRevisionId
  const selectedRevision =
    items.find((revision) => revision.id === resolvedSelectedRevisionId) ?? items[0] ?? null
  const { data: selectedDetail, isLoading: isDetailLoading } = useSkillRevision(
    skillId,
    selectedRevision?.id ?? null,
  )
  const rollback = useRollbackSkillRevision(skillId)

  function handleConfirmRollback(): void {
    if (!rollbackRevision) {
      return
    }
    rollback.mutate(rollbackRevision.id, {
      onSuccess: (response) => {
        setRollbackRevision(null)
        setSelectedRevisionId(response.revision.id)
      },
    })
  }

  return children({
    body: (
      <>
        {isLoading ? (
          <Skeleton className="h-32 w-full rounded-lg" />
        ) : items.length === 0 ? (
          <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            {t('empty')}
          </div>
        ) : (
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.85fr)]">
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
                        {revision.id === selectedRevision?.id ? (
                          <Badge variant="outline" className="moldy-ui-micro">
                            {t('selected')}
                          </Badge>
                        ) : null}
                        {(() => {
                          const passRate = revision.content_hash
                            ? passRateByContentHash.get(revision.content_hash)
                            : undefined
                          if (passRate === undefined) return null
                          return (
                            <Badge
                              variant="secondary"
                              className="moldy-ui-micro tabular-nums"
                              data-testid="revision-pass-rate"
                            >
                              {t('passRate', {
                                rate: Math.round(passRate <= 1 ? passRate * 100 : passRate),
                              })}
                            </Badge>
                          )
                        })()}
                      </div>
                      <p className="mt-1 moldy-ui-micro text-muted-foreground">
                        {t(`operation.${revision.operation}`)} ·{' '}
                        {formatDisplayDateTime(revision.created_at)}
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setSelectedRevisionId(revision.id)}
                      aria-label={t('viewRevision', { number: revision.revision_number })}
                    >
                      {t('view')}
                    </Button>
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
            <div className="space-y-3">
              <SkillHistoryDetailPanel
                revision={selectedRevision}
                detail={selectedDetail}
                currentRevisionId={currentRevisionId}
                isLoading={isDetailLoading}
                rollbackPending={rollback.isPending}
                onRequestRollback={setRollbackRevision}
              />
              {selectedRevision ? (
                <SkillRevisionDiffCard
                  skillId={skillId}
                  revision={selectedRevision}
                  detail={selectedDetail}
                />
              ) : null}
            </div>
          </div>
        )}
      </>
    ),
    footer: null,
    overlay: (
      <DeleteConfirmDialog
        open={rollbackRevision !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRollbackRevision(null)
          }
        }}
        title={t('rollbackTitle', { number: rollbackRevision?.revision_number ?? 0 })}
        description={t('rollbackDescription')}
        confirmLabel={t('rollbackConfirm')}
        isPending={rollback.isPending}
        onConfirm={handleConfirmRollback}
      />
    ),
  })
}
