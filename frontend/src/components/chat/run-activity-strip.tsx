'use client'

import {
  BrainIcon,
  CircleAlertIcon,
  FileTextIcon,
  ListChecksIcon,
  Minimize2Icon,
  NetworkIcon,
  WrenchIcon,
  type LucideIcon,
} from 'lucide-react'
import { useMemo } from 'react'
import { useTranslations } from 'next-intl'
import {
  CollapsiblePill,
  type PillKind,
  type PillStatus,
} from '@/components/chat/tool-ui/collapsible-pill'
import { cn } from '@/lib/utils'
import type {
  RunActivity,
  RunActivityKind,
  RunActivityStatus,
} from '@/lib/chat/langgraph-runtime/activity-model'

const MAX_VISIBLE_ACTIVITIES = 3

const ACTIVE_STATUSES: readonly RunActivityStatus[] = ['pending', 'running', 'requires_action']

const DEPTH_CLASS = ['', 'pl-3', 'pl-6', 'pl-9'] as const

const KIND_ICON: Record<RunActivityKind, LucideIcon> = {
  thinking: BrainIcon,
  planning: ListChecksIcon,
  tool: WrenchIcon,
  subagent: NetworkIcon,
  background_subagent: NetworkIcon,
  artifact: FileTextIcon,
  memory: BrainIcon,
  compaction: Minimize2Icon,
  interrupt: CircleAlertIcon,
  checkpoint: FileTextIcon,
  responding: BrainIcon,
  reconnecting: CircleAlertIcon,
  done: ListChecksIcon,
  error: CircleAlertIcon,
}

type ActivityTranslator = ReturnType<typeof useTranslations>

interface RunActivityStripProps {
  readonly activities: readonly RunActivity[]
  readonly className?: string
}

function isActiveStatus(status: RunActivityStatus): boolean {
  return ACTIVE_STATUSES.includes(status)
}

function selectVisibleActivities(activities: readonly RunActivity[]): readonly RunActivity[] {
  const active = activities.filter((activity) => isActiveStatus(activity.status))
  const recent = activities.filter((activity) => !isActiveStatus(activity.status))
  return [...active, ...recent].slice(0, MAX_VISIBLE_ACTIVITIES)
}

function pillStatus(status: RunActivityStatus): PillStatus {
  if (status === 'complete') return 'success'
  if (status === 'error') return 'error'
  if (status === 'cancelled') return 'cancelled'
  return 'loading'
}

function pillKind(kind: RunActivityKind): PillKind {
  if (kind === 'tool') return 'tool'
  if (kind === 'subagent' || kind === 'background_subagent') return 'subagent'
  return 'thinking'
}

function activityLabel(t: ActivityTranslator, activity: RunActivity): string {
  if (activity.kind === 'tool') return t('tool', { name: activity.title })
  if (activity.kind === 'subagent' || activity.kind === 'background_subagent') {
    return t('subagent', { name: activity.title })
  }
  return t(activity.kind)
}

function namespaceMeta(activity: RunActivity): string | undefined {
  if (activity.subtitle) return activity.subtitle
  if (activity.namespace.length <= 1) return undefined
  return activity.namespace.slice(0, -1).join(' / ')
}

function depthClass(activity: RunActivity): string {
  const depth = Math.min(activity.namespace.length, DEPTH_CLASS.length - 1)
  return DEPTH_CLASS[depth]
}

export function RunActivityStrip({ activities, className }: RunActivityStripProps) {
  const t = useTranslations('chat.activity')
  const visibleActivities = useMemo(() => selectVisibleActivities(activities), [activities])
  if (visibleActivities.length === 0) return null

  return (
    <div className={cn('flex w-full flex-col gap-1.5', className)} data-testid="run-activity-strip">
      {visibleActivities.map((activity) => (
        <div
          key={activity.id}
          className={cn('min-w-0', depthClass(activity))}
          data-kind={activity.kind}
          data-status={activity.status}
        >
          <CollapsiblePill
            status={pillStatus(activity.status)}
            kind={pillKind(activity.kind)}
            title={activityLabel(t, activity)}
            meta={namespaceMeta(activity)}
            leadingIcon={KIND_ICON[activity.kind]}
          />
        </div>
      ))}
    </div>
  )
}
