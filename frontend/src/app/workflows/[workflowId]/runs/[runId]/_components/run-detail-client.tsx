'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { PageShell } from '@/components/shared/page-shell'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { RunApprovalCard } from '@/features/workflows/components/run-approval-card'
import { RunStatusChip, StepStatusIcon } from '@/features/workflows/components/run-status'
import { branchLabel } from '@/features/workflows/lib/node-catalog'
import {
  useCancelWorkflowRun,
  useWorkflow,
  useWorkflowRun,
  useWorkflowVersion,
} from '@/lib/hooks/use-workflows'
import { cn } from '@/lib/utils'
import { formatDisplayDateTime } from '@/lib/utils/display-format'
import { RUN_TERMINAL, type WorkflowRunDetail, type WorkflowStepRun } from '@/lib/types/workflow'

/** 실행 상세 (appendix D): summary, step timeline, the selected step, the approval card. */
export function RunDetailClient({ workflowId, runId }: { workflowId: string; runId: string }) {
  const t = useTranslations('workflows.runs')
  const run = useWorkflowRun(runId)
  const workflow = useWorkflow(workflowId)
  if (run.isPending) {
    return (
      <div className="flex flex-1 flex-col gap-4 p-6">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    )
  }
  if (run.isError) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <ApiErrorState error={run.error} onRetry={() => void run.refetch()} />
      </div>
    )
  }
  return (
    <PageShell
      title={workflow.data?.name ?? t('detailTitle')}
      description={t('detailDescription')}
      action={
        <Button variant="outline" render={<Link href={`/workflows/${workflowId}/runs`} />}>
          {t('backToRuns')}
        </Button>
      }
    >
      <RunBody workflowId={workflowId} run={run.data} />
    </PageShell>
  )
}

function RunBody({ workflowId, run }: { workflowId: string; run: WorkflowRunDetail }) {
  const t = useTranslations('workflows.runs')
  const tk = useTranslations('workflows')
  const version = useWorkflowVersion(workflowId, run.version_no)
  const cancel = useCancelWorkflowRun(run.id)
  // Steps are told apart by id: a loop runs the same node again (one row per round), and a
  // for_each/parallel step's children share its position.
  const [selected, setSelected] = useState<string | null>(null)
  const nodes = new Map((version.data?.definition.nodes ?? []).map((node) => [node.id, node]))
  const labelOf = (nodeId: string) => {
    const node = nodes.get(nodeId)
    return node?.label || (node ? tk(`kind.${node.type}`) : nodeId)
  }
  const stepLabel = (step: WorkflowStepRun) =>
    step.parent_step_id ? childLabel(step, tk) : labelOf(step.node_id)
  const finished = RUN_TERMINAL.includes(run.status)
  const main = run.steps.filter((step) => !step.parent_step_id)
  const childrenOf = (step: WorkflowStepRun) =>
    run.steps.filter((child) => child.parent_step_id === step.id)
  const completed = main.filter((step) => step.status === 'completed').length
  const current =
    run.steps.find((step) => step.id === selected) ??
    main.find((step) => step.status !== 'completed') ??
    main.at(-1)
  return (
    <div className="flex flex-col gap-4">
      <section className="moldy-panel flex flex-wrap items-center gap-4 p-4">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex items-center gap-2">
            <RunStatusChip status={run.status} />
            {run.current_node && !finished ? (
              <span className="text-sm">{t('atNode', { node: labelOf(run.current_node) })}</span>
            ) : null}
          </div>
          {run.parent_conversation_id ? (
            <p className="text-xs">
              {run.parent_agent_id ? (
                <Link
                  href={`/agents/${run.parent_agent_id}/conversations/${run.parent_conversation_id}`}
                  className="text-primary-strong underline"
                >
                  {t('fromChat')}
                </Link>
              ) : (
                t('fromChat')
              )}
            </p>
          ) : null}
          <p className="text-xs text-muted-foreground">
            {t('meta', {
              id: run.id.slice(0, 8),
              version: run.version_no ?? '-',
              time: formatDisplayDateTime(run.created_at),
            })}
          </p>
          {run.error_message ? (
            <p className="text-sm text-status-danger">{run.error_message}</p>
          ) : null}
        </div>
        <div className="flex flex-col items-end">
          <span className="text-2xl font-semibold">{completed}</span>
          <span className="text-xs text-muted-foreground">{t('stepsDone')}</span>
        </div>
        {!finished ? (
          <Button
            variant="outline"
            disabled={cancel.isPending || run.status === 'canceling'}
            onClick={() =>
              cancel.mutate(undefined, { onError: () => toast.error(t('cancelFailed')) })
            }
          >
            {t('cancel')}
          </Button>
        ) : null}
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <ol
          className="moldy-panel flex flex-col gap-1 p-3 lg:col-span-1"
          aria-label={t('timeline')}
        >
          {main.map((step) => (
            <li key={step.id}>
              <StepButton
                step={step}
                label={labelOf(step.node_id)}
                active={current?.id === step.id}
                onSelect={setSelected}
              />
              {childrenOf(step).length > 0 ? (
                <ol className="ml-6 flex flex-col gap-1" aria-label={t('children')}>
                  {childrenOf(step).map((child) => (
                    <li key={child.id}>
                      <StepButton
                        step={child}
                        label={childLabel(child, tk)}
                        active={current?.id === child.id}
                        onSelect={setSelected}
                      />
                    </li>
                  ))}
                </ol>
              ) : null}
            </li>
          ))}
          {run.steps.length === 0 ? (
            <li className="p-2 text-sm text-muted-foreground">{t('noSteps')}</li>
          ) : null}
        </ol>
        <div className="flex flex-col gap-4 lg:col-span-2">
          <RunApprovalCard run={run} />
          {current ? <StepDetail step={current} label={stepLabel(current)} /> : null}
          {run.output ? (
            <section className="moldy-panel flex flex-col gap-2 p-4">
              <h2 className="text-sm font-semibold">{t('output')}</h2>
              <JsonBlock value={run.output} />
            </section>
          ) : null}
          <section className="moldy-panel flex flex-col gap-2 p-4">
            <h2 className="text-sm font-semibold">{t('input')}</h2>
            <JsonBlock value={run.input} />
          </section>
        </div>
      </div>
    </div>
  )
}

function StepButton({
  step,
  label,
  active,
  onSelect,
}: {
  step: WorkflowStepRun
  label: string
  active: boolean
  onSelect: (id: string) => void
}) {
  const t = useTranslations('workflows.runs')
  const tk = useTranslations('workflows')
  return (
    <button
      type="button"
      onClick={() => onSelect(step.id)}
      className={cn(
        'flex w-full items-start gap-3 rounded-md px-2 py-2 text-left hover:bg-accent',
        active && 'bg-accent',
      )}
    >
      <StepStatusIcon status={step.status} />
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">
          {label}
          {step.round > 1 && !step.parent_step_id ? (
            <span className="ml-1 text-xs font-normal text-muted-foreground">
              {t('round', { round: step.round })}
            </span>
          ) : null}
        </span>
        <span className="block truncate text-xs text-muted-foreground">
          {stepSummary(step, tk)}
        </span>
      </span>
    </button>
  )
}

/** A child row of a for_each/parallel step: "항목 1" or the branch name. */
function childLabel(
  step: WorkflowStepRun,
  t: (key: string, values?: Record<string, string | number>) => string,
): string {
  const key = step.branch_key ?? ''
  return /^\d+$/.test(key) ? t('runs.item', { n: Number(key) + 1 }) : key
}

function stepSummary(step: WorkflowStepRun, t: (key: string) => string): string {
  if (step.error_message) return step.error_message
  if (step.branch) return branchLabel(t, step.branch)
  if (step.output) return Object.keys(step.output).join(', ')
  return t(`runs.status.${step.status}`)
}

function StepDetail({ step, label }: { step: WorkflowStepRun; label: string }) {
  const t = useTranslations('workflows.runs')
  return (
    <section className="moldy-panel flex flex-col gap-3 p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">{label}</h2>
        <RunStatusChip status={step.status} />
      </div>
      {step.error_message ? (
        <p className="text-sm text-status-danger">
          {step.error_message} ({step.error_code})
        </p>
      ) : null}
      {step.status === 'unknown' ? (
        <p className="text-sm text-muted-foreground">{t('unknownHint')}</p>
      ) : null}
      {step.output ? <JsonBlock value={step.output} /> : null}
    </section>
  )
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="moldy-muted-panel max-h-80 overflow-auto p-3 font-mono text-xs whitespace-pre-wrap">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}
