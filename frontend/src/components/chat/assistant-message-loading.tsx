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

/** custom.isStreamingMessage가 명시적으로 false면 sticky/converted 재사용으로
 *  이미 완료 표시된 메시지다. 이 경우 잔존 `running` status는 stale로 보고
 *  streaming으로 취급하지 않는다(M6 오탐 방지). */
function isExplicitlyNotStreamingMessageMetadata(metadata: unknown): boolean {
  return (
    isRecord(metadata) && isRecord(metadata.custom) && metadata.custom.isStreamingMessage === false
  )
}

function isRunningMessageStatus(status: unknown): boolean {
  return isRecord(status) && status.type === 'running'
}

/** M6 — 메시지가 "현재 스트리밍 중"인지 판정.
 *
 * 두 신호를 OR한다: (1) 우리가 직접 심는 `metadata.custom.isStreamingMessage`,
 * (2) assistant-ui status가 `running`. (2)만으로는 sticky/converted 메시지
 * 재사용 시 완료된 메시지에 stale `running`이 남아 오탐할 수 있다. 이를 두 겹으로
 * 방어한다:
 *  - 호출 컴포넌트(`StreamingMessageLoadingIndicator`)가
 *    `AuiIf condition={(s) => s.thread.isRunning}`로 감싸므로, thread가 idle이면
 *    어떤 메시지든 인디케이터가 렌더되지 않는다(주 노출 범위 제한).
 *  - 여기서는 metadata가 streaming=false라고 *명시*하면 `running` status보다
 *    우선해 streaming이 아님으로 본다(같은 턴 안의 stale running 오탐 방지). */
export function isStreamingMessageState(message: unknown): boolean {
  if (!isRecord(message)) return false
  if (isStreamingMessageMetadata(message.metadata)) return true
  if (isExplicitlyNotStreamingMessageMetadata(message.metadata)) return false
  return isRunningMessageStatus(message.status)
}

function useIsStreamingMessage(): boolean {
  return useAuiState((s) => isStreamingMessageState(s.message))
}

function currentTurnSubagentToolCallIds(activities: readonly RunActivity[]): readonly string[] {
  const ids = new Set<string>()
  for (const activity of activities) {
    if (activity.kind === 'subagent' && activity.toolCallId) ids.add(activity.toolCallId)
  }
  return Array.from(ids)
}

function isVisibleProgressActivity(activity: RunActivity): boolean {
  return (
    activity.kind !== 'interrupt' &&
    activity.kind !== 'responding' &&
    activity.status !== 'complete' &&
    activity.status !== 'cancelled'
  )
}

export function StreamingMessageLoadingIndicator({
  activities = [],
  deepAgentsState,
  className,
}: StreamingMessageLoadingIndicatorProps) {
  const isStreamingMessage = useIsStreamingMessage()
  const semanticActivities = useMemo(
    () => activities.filter((activity) => activity.kind !== 'interrupt'),
    [activities],
  )
  const progressActivities = useMemo(
    () => semanticActivities.filter(isVisibleProgressActivity),
    [semanticActivities],
  )
  const subagentToolCallIds = useMemo(
    () => currentTurnSubagentToolCallIds(semanticActivities),
    [semanticActivities],
  )
  const subagentProgress = useSubagentProgressSummary(subagentToolCallIds)
  if (!isStreamingMessage) return null
  const hasActivities = progressActivities.length > 0
  const hasState = hasDeepAgentsState(deepAgentsState)
  const hasSubagentProgress = subagentProgress.total > 0
  const hasStatusPanel = hasActivities || hasState || hasSubagentProgress

  return (
    <AuiIf condition={(s) => s.thread.isRunning}>
      {hasStatusPanel ? (
        <div className={cn('mb-1 flex flex-col gap-1.5', className)}>
          {hasState ? <DeepAgentsStatePanel state={deepAgentsState} /> : null}
          {hasSubagentProgress ? <SubagentProgress summary={subagentProgress} /> : null}
          {hasActivities ? <RunActivityStrip activities={progressActivities} /> : null}
        </div>
      ) : (
        <WittyLoadingMessage className={cn('pointer-events-none mb-1 px-1', className)} />
      )}
    </AuiIf>
  )
}
