'use client'

import { NetworkIcon } from 'lucide-react'
import { useAtomValue, useSetAtom } from 'jotai'
import { useTranslations } from 'next-intl'
import type { SubagentDiscoverySnapshot } from '@langchain/react'
import { isAgUiSubagentSnapshot } from '@/components/chat/ag-ui-subagent-details'
import { useChatConversationId } from '@/components/chat/conversation-context'
import {
  summarizeSubagentProgress,
  useSubagentSnapshots,
} from '@/lib/chat/langgraph-runtime/subagent-runtime'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import { chatSubagentNamesAtom, resolveSubagentDisplayName } from '@/lib/stores/chat-subagent-names'
import { childTaskLabels } from '@/lib/chat/child-task-labels'
import { cn } from '@/lib/utils'
import { isActiveChildTask, type ChildTask } from '@/lib/hooks/use-child-tasks'

const STATUS_DOT_CLASS: Record<SubagentDiscoverySnapshot['status'], string> = {
  running: 'bg-status-info animate-pulse',
  complete: 'bg-status-success',
  error: 'bg-status-danger',
}

function TeamChip({
  status,
  nested,
  title,
  showBrief,
  displayName,
  onOpen,
}: {
  readonly status: SubagentDiscoverySnapshot['status']
  readonly nested?: boolean
  readonly title?: string
  readonly showBrief?: boolean
  readonly displayName: string
  readonly onOpen: () => void
}) {
  const t = useTranslations('chat.teamStrip')
  return (
    <button
      type="button"
      onClick={onOpen}
      title={title || displayName}
      aria-label={t('chipLabel', { name: displayName })}
      data-moldy-team-chip={status}
      className={cn(
        'inline-flex max-w-48 shrink-0 items-center gap-1.5 rounded-full border border-border/60',
        'bg-background px-2.5 py-0.5 moldy-ui-caption text-foreground/85 transition-colors',
        'hover:bg-accent hover:text-foreground',
      )}
    >
      {/* SDK depth는 root=0, 직접 위임=1 — 서브의 서브(≥2)만 중첩 마커. */}
      {nested ? (
        <span aria-hidden className="shrink-0 text-muted-foreground">
          ↳
        </span>
      ) : null}
      <span
        aria-hidden
        className={cn('size-1.5 shrink-0 rounded-full', STATUS_DOT_CLASS[status])}
      />
      <span className="truncate">{displayName}</span>
      {showBrief && title && <span className="truncate text-muted-foreground">{title}</span>}
    </button>
  )
}

export function SubagentTeamStrip({
  tasks = [],
  allowLegacy = true,
}: {
  readonly tasks?: readonly ChildTask[]
  readonly allowLegacy?: boolean
} = {}) {
  const t = useTranslations('chat.teamStrip')
  const snapshots = useSubagentSnapshots()
  const conversationId = useChatConversationId()
  const subagentNames = useAtomValue(chatSubagentNamesAtom)
  const setRail = useSetAtom(chatRightRailAtom)

  if ((!allowLegacy || snapshots.length === 0) && tasks.length === 0) return null

  const names = conversationId ? subagentNames[conversationId] : undefined
  const labels = childTaskLabels(tasks, names)
  const transient = allowLegacy ? snapshots : []
  const summary = summarizeSubagentProgress(transient)
  const running = summary.running + tasks.filter(isActiveChildTask).length
  const total = transient.length + tasks.length

  return (
    <div
      className="border-b border-border/60 bg-background/95 px-4 py-1.5"
      data-moldy-team-strip="true"
    >
      <div className="mx-auto flex w-full max-w-3xl items-center gap-2">
        <span className="flex shrink-0 items-center gap-1.5 moldy-ui-caption font-medium text-muted-foreground">
          <NetworkIcon aria-hidden className="size-3.5" />
          {t('title')}
        </span>
        <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto">
          {tasks.map((task) => {
            const displayName = labels.get(task.id) ?? task.role
            return (
              <TeamChip
                key={task.id}
                status={
                  isActiveChildTask(task)
                    ? 'running'
                    : task.status === 'completed'
                      ? 'complete'
                      : 'error'
                }
                displayName={displayName}
                title={task.brief ?? undefined}
                showBrief={displayName !== resolveSubagentDisplayName(names, task.role)}
                onOpen={() =>
                  setRail({
                    mode: 'subagent',
                    subagent: {
                      conversationId,
                      childTaskId: task.id,
                      toolCallId: task.tool_call_id,
                      agentName: displayName,
                    },
                  })
                }
              />
            )
          })}
          {transient.map((snapshot) => {
            const displayName = resolveSubagentDisplayName(names, snapshot.name)
            const runId = isAgUiSubagentSnapshot(snapshot) ? snapshot.subagentRunId : undefined
            return (
              <TeamChip
                key={runId ?? snapshot.id}
                status={snapshot.status}
                nested={snapshot.depth > 1}
                title={snapshot.taskInput ?? undefined}
                displayName={displayName}
                onOpen={() =>
                  setRail({
                    mode: 'subagent',
                    subagent: {
                      conversationId,
                      toolCallId: snapshot.id,
                      ...(runId !== undefined
                        ? { subagentRunId: runId, agentName: '' }
                        : { agentName: displayName, input: snapshot.taskInput ?? '' }),
                    },
                  })
                }
              />
            )
          })}
        </div>
        <span className="shrink-0 moldy-ui-caption text-muted-foreground">
          {running > 0
            ? t('runningMeta', { count: running })
            : t('doneMeta', { done: total, total })}
        </span>
      </div>
    </div>
  )
}
