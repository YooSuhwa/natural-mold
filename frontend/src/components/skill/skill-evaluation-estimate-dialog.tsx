'use client'

import { Clock3 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Checkbox } from '@/components/ui/checkbox'
import { cn } from '@/lib/utils'
import { formatDisplayUsd } from '@/lib/utils/display-format'
import type { SkillEvaluationRunEstimate } from '@/lib/types/skill-evaluation'

type SkillEvaluationEstimateDialogProps = {
  readonly open: boolean
  readonly setName: string
  readonly estimate: SkillEvaluationRunEstimate | null
  readonly isPending: boolean
  readonly isEstimating: boolean
  readonly baselineComparison: boolean
  readonly onBaselineComparisonChange: (next: boolean) => void
  readonly onOpenChange: (open: boolean) => void
  readonly onConfirm: () => void
}

const BASELINE_TOGGLE_ID = 'skill-evaluation-baseline-toggle'

function roundedSeconds(value: number): number {
  return Math.max(1, Math.ceil(value))
}

export function SkillEvaluationEstimateDialog({
  open,
  setName,
  estimate,
  isPending,
  isEstimating,
  baselineComparison,
  onBaselineComparisonChange,
  onOpenChange,
  onConfirm,
}: SkillEvaluationEstimateDialogProps) {
  const t = useTranslations('skill.detailDialog.evaluation')

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogMedia>
            <Clock3 aria-hidden="true" />
          </AlertDialogMedia>
          <AlertDialogTitle>{t('estimateTitle')}</AlertDialogTitle>
          <AlertDialogDescription>
            {t('estimateDescription', { name: setName })}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {estimate ? (
          <div className="grid gap-2">
            <dl
              aria-busy={isEstimating}
              className={cn(
                'grid gap-2 rounded-lg border border-border/70 bg-muted/30 p-3 text-sm transition-opacity',
                isEstimating ? 'opacity-60' : null,
              )}
            >
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted-foreground">{t('estimateCaseCount')}</dt>
                <dd className="font-medium">{estimate.case_count}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted-foreground">{t('estimateModelCalls')}</dt>
                <dd className="font-medium">{estimate.model_call_count}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted-foreground">{t('estimateDuration')}</dt>
                <dd className="font-medium">
                  {t('estimateSeconds', { count: roundedSeconds(estimate.estimated_seconds) })}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted-foreground">{t('estimateTimeout')}</dt>
                <dd className="font-medium">
                  {t('estimateSeconds', { count: roundedSeconds(estimate.timeout_seconds) })}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted-foreground">{t('estimateCost')}</dt>
                <dd className="font-medium" data-testid="estimate-cost">
                  {estimate.pricing_available
                    ? formatDisplayUsd(estimate.estimated_cost_usd)
                    : t('estimateCostUnpriced')}
                </dd>
              </div>
              {estimate.pricing_available && estimate.runner_model ? (
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-muted-foreground">{t('estimateRunnerModel')}</dt>
                  <dd className="max-w-48 truncate font-mono text-xs" title={estimate.runner_model}>
                    {estimate.runner_model}
                  </dd>
                </div>
              ) : null}
            </dl>
            <label
              htmlFor={BASELINE_TOGGLE_ID}
              className="flex items-center justify-between gap-3 rounded-lg border border-border/70 bg-muted/30 p-3 text-sm"
            >
              <span className="flex flex-col gap-0.5">
                <span className="font-medium">{t('estimateBaseline')}</span>
                <span className="text-xs text-muted-foreground">{t('estimateBaselineHint')}</span>
              </span>
              <Checkbox
                id={BASELINE_TOGGLE_ID}
                checked={baselineComparison}
                onCheckedChange={(next) => onBaselineComparisonChange(Boolean(next))}
                disabled={isPending || isEstimating}
                data-testid="estimate-baseline-toggle"
              />
            </label>
          </div>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>{t('estimateCancel')}</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm} disabled={isPending || isEstimating}>
            {isPending ? t('runAgainPending') : t('estimateConfirm')}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
