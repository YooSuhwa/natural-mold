'use client'

import { memo } from 'react'
import { AlertTriangleIcon, CheckIcon, CircleDotIcon, ClockIcon, XCircleIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'

export type PhaseStatus = 'pending' | 'active' | 'completed' | 'failed' | 'warning'

export interface PhaseState {
  id: number
  status: PhaseStatus
  subAgentName?: string
  resultSummary?: string
}

function PhaseIcon({ status }: { status: PhaseStatus }) {
  if (status === 'completed') {
    return (
      <div className="flex size-6 items-center justify-center rounded-full bg-emerald-500 text-white">
        <CheckIcon className="size-3.5" />
      </div>
    )
  }
  if (status === 'active') {
    return (
      <div className="flex size-6 items-center justify-center rounded-full bg-primary text-primary-foreground">
        <CircleDotIcon className="size-3.5 animate-pulse" />
      </div>
    )
  }
  if (status === 'warning') {
    return (
      <div className="flex size-6 items-center justify-center rounded-full bg-amber-500 text-white">
        <AlertTriangleIcon className="size-3.5" />
      </div>
    )
  }
  if (status === 'failed') {
    return (
      <div className="flex size-6 items-center justify-center rounded-full bg-destructive text-destructive-foreground">
        <XCircleIcon className="size-3.5" />
      </div>
    )
  }
  return (
    <div className="flex size-6 items-center justify-center rounded-full border-2 border-muted-foreground/30 text-muted-foreground/50">
      <ClockIcon className="size-3" />
    </div>
  )
}

function PhaseStatusBadge({ status }: { status: PhaseStatus }) {
  const t = useTranslations('agent.creation.status')
  const styles: Record<PhaseStatus, string> = {
    completed: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-400',
    active: 'bg-primary/10 text-primary',
    warning: 'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-400',
    failed: 'bg-destructive/10 text-destructive',
    pending: 'bg-muted text-muted-foreground',
  }
  return (
    <span className={cn('shrink-0 rounded-md px-2 py-0.5 text-xs font-medium', styles[status])}>
      {t(status)}
    </span>
  )
}

export const PhaseTimeline = memo(function PhaseTimeline({ phases }: { phases: PhaseState[] }) {
  const t = useTranslations('agent.creation')

  const PHASE_META = [
    { label: t('phase1.label'), description: t('phase1.description') },
    { label: t('phase2.label'), description: t('phase2.description') },
    { label: t('phase3.label'), description: t('phase3.description') },
    { label: t('phase4.label'), description: t('phase4.description') },
    { label: t('phase5.label'), description: t('phase5.description') },
    { label: t('phase6.label'), description: t('phase6.description') },
    { label: t('phase7.label'), description: t('phase7.description') },
  ]

  return (
    <div className="rounded-xl border bg-muted/30 p-4" aria-live="polite">
      <h3 className="mb-3 text-sm font-medium text-muted-foreground">{t('progress')}</h3>
      <div className="space-y-0" role="list" aria-label={t('progress')}>
        {phases.map((phase, idx) => {
          const meta = PHASE_META[idx]
          const isLast = idx === phases.length - 1
          return (
            <div
              key={phase.id}
              className="flex gap-3"
              role="listitem"
              aria-current={phase.status === 'active' ? 'step' : undefined}
            >
              <div className="flex flex-col items-center">
                <PhaseIcon status={phase.status} />
                {!isLast && (
                  <div
                    className={cn(
                      'w-0.5 min-h-4 flex-1',
                      phase.status === 'completed'
                        ? 'bg-emerald-500'
                        : phase.status === 'warning'
                          ? 'bg-amber-500'
                          : phase.status === 'failed'
                            ? 'bg-destructive'
                            : 'bg-muted-foreground/20',
                    )}
                  />
                )}
              </div>
              <div className="flex flex-1 items-start justify-between pb-4">
                <div className="flex-1">
                  <p
                    className={cn(
                      'text-sm leading-6',
                      phase.status === 'active'
                        ? 'font-semibold text-foreground'
                        : phase.status === 'completed'
                          ? 'font-medium text-foreground'
                          : phase.status === 'warning'
                            ? 'font-medium text-amber-700 dark:text-amber-400'
                            : phase.status === 'failed'
                              ? 'font-medium text-destructive'
                              : 'text-muted-foreground',
                    )}
                  >
                    Phase {phase.id}: {meta.label}
                    <span className="ml-1.5 font-normal text-muted-foreground">
                      {meta.description}
                    </span>
                  </p>
                  {phase.status === 'active' && phase.subAgentName && (
                    <p className="mt-0.5 text-xs text-primary animate-pulse">
                      {t('subAgentWorking', { name: phase.subAgentName })}
                    </p>
                  )}
                  {phase.status === 'completed' && phase.resultSummary && (
                    <p className="mt-0.5 text-xs text-muted-foreground">{phase.resultSummary}</p>
                  )}
                  {phase.status === 'warning' && phase.resultSummary && (
                    <p className="mt-0.5 text-xs text-amber-600 dark:text-amber-400">
                      {phase.resultSummary}
                    </p>
                  )}
                </div>
                <PhaseStatusBadge status={phase.status} />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
})
