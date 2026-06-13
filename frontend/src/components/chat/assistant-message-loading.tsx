'use client'

import { AuiIf, useAuiState } from '@assistant-ui/react'
import { useMemo } from 'react'
import { DeepAgentsStatePanel } from '@/components/chat/deepagents-state-panel'
import { RunActivityStrip } from '@/components/chat/run-activity-strip'
import { SubagentProgress } from '@/components/chat/subagent-progress'
import { WittyLoadingMessage } from '@/components/chat/witty-loading'
import { cn } from '@/lib/utils'
import type { RunActivity } from '@/lib/chat/langgraph-runtime/activity-model'
import type { DeepAgentsStateSnapshot } from '@/lib/chat/langgraph-runtime/deepagents-state'
import { hasDeepAgentsState } from '@/lib/chat/langgraph-runtime/deepagents-state'
import { useSubagentProgressSummary } from '@/lib/chat/langgraph-runtime/subagent-runtime'

interface StreamingMessageLoadingIndicatorProps {
  readonly activities?: readonly RunActivity[]
  readonly deepAgentsState?: DeepAgentsStateSnapshot
  readonly className?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isStreamingMessageMetadata(metadata: unknown): boolean {
  if (!isRecord(metadata) || !isRecord(metadata.custom)) return false
  return metadata.custom.isStreamingMessage === true
}

function useIsStreamingMessage(): boolean {
  return useAuiState((s) => isStreamingMessageMetadata(s.message?.metadata))
}

function currentTurnSubagentToolCallIds(activities: readonly RunActivity[]): readonly string[] {
  const ids = new Set<string>()
  for (const activity of activities) {
    if (activity.kind === 'subagent' && activity.toolCallId) ids.add(activity.toolCallId)
  }
  return Array.from(ids)
}

export function StreamingMessageLoadingIndicator({
  activities = [],
  deepAgentsState,
  className,
}: StreamingMessageLoadingIndicatorProps) {
  const isStreamingMessage = useIsStreamingMessage()
  const subagentToolCallIds = useMemo(
    () => currentTurnSubagentToolCallIds(activities),
    [activities],
  )
  const subagentProgress = useSubagentProgressSummary(subagentToolCallIds)
  if (!isStreamingMessage) return null
  const hasActivities = activities.length > 0
  const hasState = hasDeepAgentsState(deepAgentsState)
  const hasSubagentProgress = subagentProgress.total > 0

  return (
    <AuiIf condition={(s) => s.thread.isRunning}>
      {hasActivities || hasState || hasSubagentProgress ? (
        <div className={cn('mb-1 flex flex-col gap-1.5', className)}>
          {hasState ? <DeepAgentsStatePanel state={deepAgentsState} /> : null}
          {hasSubagentProgress ? <SubagentProgress summary={subagentProgress} /> : null}
          {hasActivities ? <RunActivityStrip activities={activities} /> : null}
        </div>
      ) : (
        <WittyLoadingMessage className={cn('pointer-events-none mb-1 px-1', className)} />
      )}
    </AuiIf>
  )
}
