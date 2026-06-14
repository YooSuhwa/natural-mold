'use client'

import { useMemo, useState } from 'react'
import { CircleStop, KeyRound, RefreshCw } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  useCancelSkillEvaluationRun,
  useCreateSkillEvaluationRun,
  useEstimateSkillEvaluationRun,
  useSkillEvaluationSets,
} from '@/lib/hooks/use-skill-evaluations'
import type {
  SkillEvaluationRunEstimate,
  SkillEvaluationRunStatus,
  SkillEvaluationSet,
} from '@/lib/types/skill-evaluation'

import { SkillEvaluationEstimateDialog } from './skill-evaluation-estimate-dialog'
import { SkillEvaluationSummaryBadge } from './skill-evaluation-summary-badge'

const CANCELLABLE_RUN_STATUS: Record<SkillEvaluationRunStatus, boolean> = {
  queued: true,
  running: true,
  grading: true,
  completed: false,
  failed: false,
  cancelled: false,
}

export function SkillEvaluationTab({
  skillId,
  onClose,
  needsCredentialSetup = false,
  onOpenCredentials = noop,
}: {
  readonly skillId: string
  readonly onClose: () => void
  readonly needsCredentialSetup?: boolean
  readonly onOpenCredentials?: () => void
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
              <SkillEvaluationSetCard
                key={set.id}
                skillId={skillId}
                set={set}
                needsCredentialSetup={needsCredentialSetup}
                onOpenCredentials={onOpenCredentials}
              />
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

function SkillEvaluationSetCard({
  skillId,
  set,
  needsCredentialSetup,
  onOpenCredentials,
}: {
  readonly skillId: string
  readonly set: SkillEvaluationSet
  readonly needsCredentialSetup: boolean
  readonly onOpenCredentials: () => void
}) {
  const t = useTranslations('skill.detailDialog.evaluation')
  const runAgain = useCreateSkillEvaluationRun(skillId, set.id)
  const estimateRun = useEstimateSkillEvaluationRun(skillId, set.id)
  const cancelRun = useCancelSkillEvaluationRun(skillId, set.id)
  const [estimate, setEstimate] = useState<SkillEvaluationRunEstimate | null>(null)
  const [estimateOpen, setEstimateOpen] = useState(false)
  const latestRun = set.latest_run
  const canCancel = latestRun ? CANCELLABLE_RUN_STATUS[latestRun.status] : false
  const isMutating = runAgain.isPending || estimateRun.isPending || cancelRun.isPending

  function requestRunAgain(): void {
    estimateRun.mutate(undefined, {
      onSuccess: (nextEstimate) => {
        setEstimate(nextEstimate)
        setEstimateOpen(true)
      },
    })
  }

  function confirmRunAgain(): void {
    runAgain.mutate(undefined, {
      onSuccess: () => setEstimateOpen(false),
    })
  }

  return (
    <article className="rounded-lg border border-border/70 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold">{set.name}</h3>
          {set.description ? (
            <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{set.description}</p>
          ) : null}
        </div>
        <SkillEvaluationSummaryBadge
          summary={
            latestRun
              ? {
                  status: latestRun.status,
                  latest_run_id: latestRun.id,
                  evaluation_set_id: set.id,
                  pass_rate:
                    typeof latestRun.summary?.pass_rate === 'number'
                      ? latestRun.summary.pass_rate
                      : null,
                  skill_content_hash: latestRun.skill_content_hash ?? null,
                  created_at: latestRun.created_at,
                  completed_at: latestRun.completed_at ?? null,
                }
              : { status: 'missing' }
          }
        />
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {t('caseCount', { count: set.evals.length })}
        </p>
        {canCancel && latestRun ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={isMutating}
            aria-label={t('cancelRunFor', { name: set.name })}
            onClick={() =>
              cancelRun.mutate({
                runId: latestRun.id,
                data: { reason: 'user_requested' },
              })
            }
          >
            <CircleStop aria-hidden="true" />
            {cancelRun.isPending ? t('cancelPending') : t('cancelRun')}
          </Button>
        ) : needsCredentialSetup ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label={t('connectCredentialsFor', { name: set.name })}
            onClick={onOpenCredentials}
          >
            <KeyRound aria-hidden="true" />
            {t('connectCredentials')}
          </Button>
        ) : (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={isMutating}
            aria-label={t('runAgainFor', { name: set.name })}
            onClick={requestRunAgain}
          >
            <RefreshCw aria-hidden="true" />
            {estimateRun.isPending ? t('estimatePending') : t('runAgain')}
          </Button>
        )}
      </div>
      <SkillEvaluationEstimateDialog
        open={estimateOpen}
        setName={set.name}
        estimate={estimate}
        isPending={runAgain.isPending}
        onOpenChange={setEstimateOpen}
        onConfirm={confirmRunAgain}
      />
    </article>
  )
}

function noop(): void {}
