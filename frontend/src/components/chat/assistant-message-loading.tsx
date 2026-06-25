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

function isRunningMessageStatus(status: unknown): boolean {
  return isRecord(status) && status.type === 'running'
}

/** M6 — 메시지가 "현재 스트리밍 중"인지 판정.
 *
 * 두 신호를 OR한다: (1) 우리가 직접 심는 `metadata.custom.isStreamingMessage`,
 * (2) assistant-ui status가 `running`.
 *
 * `metadata.custom.isStreamingMessage`는 `convert-message.ts`가 id가 `stream-`로
 * 시작하는 메시지에 대해서만 `true`로 심으며, `false`를 명시하는 production 경로는
 * 없다(완료 시엔 필드가 단순히 부재한다). 따라서 "metadata가 streaming=false라고
 * 명시하면 running status를 무시한다"는 식의 추가 가드는 런타임에서 절대 발화될 수
 * 없는 dead code였다 — 이를 제거하고 위 두 신호의 단순 OR로 되돌렸다.
 *
 * sticky/converted 재사용으로 완료 메시지에 stale `running` status가 남을 수 있다는
 * 우려는 호출 컴포넌트(`StreamingMessageLoadingIndicator`)가
 * `AuiIf condition={(s) => s.thread.isRunning}`로 감싸 thread가 idle이면 어떤
 * 메시지든 인디케이터가 렌더되지 않는 것으로 방어한다. */
export function isStreamingMessageState(message: unknown): boolean {
  if (!isRecord(message)) return false
  if (isStreamingMessageMetadata(message.metadata)) return true
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
