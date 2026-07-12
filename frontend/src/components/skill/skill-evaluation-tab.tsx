'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useSkillEvaluationRuns, useSkillEvaluationSets } from '@/lib/hooks/use-skill-evaluations'
import type { SkillEvaluationRun, SkillEvaluationSet } from '@/lib/types/skill-evaluation'

import { SkillEvaluationRunDetail } from './skill-evaluation-run-detail'
import { SkillEvaluationSetCard } from './skill-evaluation-set-card'

type SkillEvaluationTabProps = {
  readonly currentSkillContentHash?: string | null
  readonly needsCredentialSetup?: boolean
  readonly onOpenCredentials?: () => void
  readonly skillContentHash?: string | null
  readonly skillId: string
}

function sortRunsNewestFirst(runs: readonly SkillEvaluationRun[]): readonly SkillEvaluationRun[] {
  return [...runs].sort((a, b) => b.created_at.localeCompare(a.created_at))
}

function firstRunForSet(set: SkillEvaluationSet | null): SkillEvaluationRun | null {
  return set?.latest_run ?? null
}

function noop(): void {}

export function SkillEvaluationTab({
  currentSkillContentHash,
  needsCredentialSetup = false,
  onOpenCredentials = noop,
  skillContentHash,
  skillId,
}: SkillEvaluationTabProps) {
  const t = useTranslations('skill.detailDialog.evaluation')
  const { data: sets, isLoading } = useSkillEvaluationSets(skillId)
  const evaluationSets = useMemo(() => sets ?? [], [sets])
  const [selectedSetId, setSelectedSetId] = useState<string | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const selectedSet =
    evaluationSets.find((set) => set.id === selectedSetId) ?? evaluationSets[0] ?? null
  const runsQuery = useSkillEvaluationRuns(skillId, selectedSet?.id ?? null)
  const runFallback = firstRunForSet(selectedSet)
  const runs = useMemo(
    () => sortRunsNewestFirst(runsQuery.data ?? (runFallback ? [runFallback] : [])),
    [runFallback, runsQuery.data],
  )
  const selectedRun = runs.find((run) => run.id === selectedRunId) ?? runs[0] ?? runFallback
  const resolvedContentHash = currentSkillContentHash ?? skillContentHash ?? null

  function selectSet(setId: string): void {
    setSelectedSetId(setId)
    setSelectedRunId(null)
  }

  if (isLoading) {
    return <Skeleton className="h-32 w-full rounded-lg" />
  }

  if (evaluationSets.length === 0) {
    return (
      <div className="rounded-lg border border-dashed p-6 text-center">
        <p className="text-sm font-medium">{t('emptyTitle')}</p>
        <p className="mt-1 text-xs text-muted-foreground">{t('emptyDescription')}</p>
      </div>
    )
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(20rem,1fr)]">
      <div className="space-y-4">
        <div className="space-y-3">
          {evaluationSets.map((set) => (
            <SkillEvaluationSetCard
              key={set.id}
              currentSkillContentHash={resolvedContentHash}
              needsCredentialSetup={needsCredentialSetup}
              onOpenCredentials={onOpenCredentials}
              onSelect={() => selectSet(set.id)}
              selected={selectedSet?.id === set.id}
              set={set}
              skillId={skillId}
            />
          ))}
        </div>
        <section className="rounded-lg border border-border/70 p-3">
          <h3 className="text-sm font-semibold">{t('runHistoryTitle')}</h3>
          {runsQuery.isLoading ? (
            <Skeleton className="mt-3 h-20 w-full rounded-lg" />
          ) : runs.length === 0 ? (
            <p className="mt-2 text-xs text-muted-foreground">{t('noRunHistory')}</p>
          ) : (
            <div className="mt-3 space-y-2">
              {runs.map((run) => (
                <Button
                  key={run.id}
                  type="button"
                  variant={selectedRun?.id === run.id ? 'secondary' : 'outline'}
                  size="sm"
                  className="w-full justify-start font-mono"
                  aria-label={t('viewRun', { id: run.id })}
                  onClick={() => setSelectedRunId(run.id)}
                >
                  {run.id}
                </Button>
              ))}
            </div>
          )}
        </section>
      </div>
      <SkillEvaluationRunDetail
        currentSkillContentHash={resolvedContentHash}
        isLoading={runsQuery.isLoading}
        run={selectedRun}
      />
    </div>
  )
}
