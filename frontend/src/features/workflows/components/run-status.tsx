'use client'

import {
  CircleCheckIcon,
  CircleDashedIcon,
  CircleHelpIcon,
  CircleXIcon,
  LoaderCircleIcon,
  PauseCircleIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'

import { cn } from '@/lib/utils'
import type { WorkflowRunStatus, WorkflowStepStatus } from '@/lib/types/workflow'

const TONE: Record<WorkflowRunStatus | WorkflowStepStatus, string> = {
  queued: 'moldy-status-info',
  running: 'moldy-status-info',
  waiting: 'moldy-status-warn',
  unknown: 'moldy-status-warn',
  completed: 'moldy-status-success',
  failed: 'moldy-status-danger',
  canceling: 'moldy-status-warn',
  canceled: 'moldy-status-warn',
}

export function RunStatusChip({ status }: { status: WorkflowRunStatus | WorkflowStepStatus }) {
  const t = useTranslations('workflows.runs')
  return (
    <span className={cn('moldy-status-surface rounded-full px-2 py-0.5 text-xs', TONE[status])}>
      {t(`status.${status}`)}
    </span>
  )
}

export function StepStatusIcon({ status }: { status: WorkflowStepStatus }) {
  const className = 'size-5 shrink-0'
  switch (status) {
    case 'completed':
      return <CircleCheckIcon className={cn(className, 'text-status-success')} aria-hidden />
    case 'failed':
      return <CircleXIcon className={cn(className, 'text-status-danger')} aria-hidden />
    case 'waiting':
      return <PauseCircleIcon className={cn(className, 'text-status-warn')} aria-hidden />
    case 'unknown':
      return <CircleHelpIcon className={cn(className, 'text-status-warn')} aria-hidden />
    case 'running':
      return (
        <LoaderCircleIcon className={cn(className, 'animate-spin text-status-info')} aria-hidden />
      )
    default:
      return <CircleDashedIcon className={cn(className, 'text-muted-foreground')} aria-hidden />
  }
}
