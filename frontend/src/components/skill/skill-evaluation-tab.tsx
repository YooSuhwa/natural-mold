'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useSkillEvaluationSets } from '@/lib/hooks/use-skill-evaluations'

import { SkillEvaluationSummaryBadge } from './skill-evaluation-summary-badge'

export function SkillEvaluationTab({
  skillId,
  onClose,
}: {
  readonly skillId: string
  readonly onClose: () => void
}) {
  const t = useTranslations('skill.detailDialog.evaluation')
  const common = useTranslations('skill.detailDialog')
  const { data: sets, isLoading } = useSkillEvaluationSets(skillId)
  const evaluationSets = useMemo(() => sets ?? [], [sets])

  return (
    <>
      <DialogShell.Body>
        {isLoading ? (
          <Skeleton className="h-32 w-full rounded-lg" />
        ) : evaluationSets.length === 0 ? (
          <div className="rounded-lg border border-dashed p-6 text-center">
            <p className="text-sm font-medium">{t('emptyTitle')}</p>
            <p className="mt-1 text-xs text-muted-foreground">{t('emptyDescription')}</p>
          </div>
        ) : (
          <div className="space-y-3">
            {evaluationSets.map((set) => (
              <article key={set.id} className="rounded-lg border border-border/70 p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate text-sm font-semibold">{set.name}</h3>
                    {set.description ? (
                      <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                        {set.description}
                      </p>
                    ) : null}
                  </div>
                  <SkillEvaluationSummaryBadge
                    summary={
                      set.latest_run
                        ? {
                            status: set.latest_run.status,
                            latest_run_id: set.latest_run.id,
                            evaluation_set_id: set.id,
                            pass_rate:
                              typeof set.latest_run.summary?.pass_rate === 'number'
                                ? set.latest_run.summary.pass_rate
                                : null,
                            skill_content_hash: set.latest_run.skill_content_hash ?? null,
                            created_at: set.latest_run.created_at,
                            completed_at: set.latest_run.completed_at ?? null,
                          }
                        : { status: 'missing' }
                    }
                  />
                </div>
                <p className="mt-3 text-xs text-muted-foreground">
                  {t('caseCount', { count: set.evals.length })}
                </p>
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
