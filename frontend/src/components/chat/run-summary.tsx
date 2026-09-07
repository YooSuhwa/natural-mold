'use client'

import { useState } from 'react'
import { useAuiState } from '@assistant-ui/react'
import { ChevronDownIcon, Clock3Icon, ListTreeIcon, NetworkIcon, WrenchIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useChatConversationId } from '@/components/chat/conversation-context'
import { useMessageRunSummary } from '@/lib/hooks/use-message-run-summary'
import { cn } from '@/lib/utils'
import type { RunSummary, RunSummaryActivity } from '@/lib/chat/run-summary-model'

function formatRunDuration(
  elapsedMs: number | null,
  t: ReturnType<typeof useTranslations<'chat.activity'>>,
): string | null {
  if (elapsedMs === null || !Number.isFinite(elapsedMs)) return null
  const seconds = Math.max(0, elapsedMs) / 1000
  if (seconds < 60) {
    return t('durationSeconds', {
      count: seconds < 10 ? Number(seconds.toFixed(1)) : Math.round(seconds),
    })
  }
  const totalSeconds = Math.round(seconds)
  const minutes = Math.floor(totalSeconds / 60)
  const remaining = totalSeconds % 60
  return t('durationMinutesSeconds', { minutes, seconds: remaining })
}

function countText(value: number | null): string {
  return value === null ? '–' : String(value)
}

function completeTotal(root: number | null, descendant: number | null): number | null {
  return root === null || descendant === null ? null : root + descendant
}

function activityName(activity: RunSummaryActivity): string {
  return activity.name ?? activity.kind
}

function liveActivityText(
  t: ReturnType<typeof useTranslations<'chat.activity'>>,
  activity: RunSummaryActivity | undefined,
): string | null {
  if (!activity || activity.status === undefined) return null
  const name = activityName(activity)
  if (activity.kind === 'tool') return t('tool', { name })
  if (activity.kind === 'subagent' || activity.kind === 'background_subagent') {
    return t('subagent', { name })
  }
  const labels: Partial<Record<string, string>> = {
    thinking: t('thinking'),
    planning: t('planning'),
    artifact: t('artifact'),
    memory: t('memory'),
    compaction: t('compaction'),
    interrupt: t('interrupt'),
    checkpoint: t('checkpoint'),
    responding: t('responding'),
    reconnecting: t('reconnecting'),
    done: t('done'),
    error: t('error'),
  }
  return labels[activity.kind] ?? name
}

export function RunSummaryPanel({ summary }: { readonly summary: RunSummary }) {
  const t = useTranslations('chat.activity')
  const [expanded, setExpanded] = useState(false)
  const elapsed = formatRunDuration(summary.elapsedMs, t)
  const toolTotal = completeTotal(summary.rootToolCalls, summary.descendantToolCalls)
  const subagentTotal = completeTotal(summary.rootSubagentCalls, summary.descendantSubagentCalls)
  const currentActivity = summary.isLive ? summary.activities.at(-1) : undefined
  const currentActivityText = liveActivityText(t, currentActivity)

  return (
    <section
      className="moldy-muted-panel w-full overflow-hidden"
      aria-label={t('summaryAria')}
      data-run-id={summary.runId}
      data-testid="run-summary"
    >
      <div className="flex min-h-9 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 moldy-ui-caption text-muted-foreground">
        <span className="inline-flex items-center gap-1 tabular-nums">
          <Clock3Icon className="size-3.5" aria-hidden />
          {elapsed ?? t('elapsedUnknown')}
        </span>
        <span className="inline-flex items-center gap-1 tabular-nums">
          <WrenchIcon className="size-3.5" aria-hidden />
          {t('toolTotal', { count: countText(toolTotal) })}
        </span>
        <span className="inline-flex items-center gap-1 tabular-nums">
          <NetworkIcon className="size-3.5" aria-hidden />
          {t('subagentTotal', { count: countText(subagentTotal) })}
        </span>
        {currentActivityText ? (
          <span
            className="min-w-0 truncate text-foreground"
            data-kind={currentActivity?.kind}
            data-status={currentActivity?.status}
          >
            {currentActivityText}
          </span>
        ) : null}
        <button
          type="button"
          className="ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-medium text-foreground transition-colors hover:bg-accent"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? t('hideDetails') : t('showDetails')}
          <ChevronDownIcon
            className={cn('size-3.5 transition-transform', expanded && 'rotate-180')}
            aria-hidden
          />
        </button>
      </div>
      {expanded ? (
        <div className="border-t px-3 py-2">
          <dl className="mb-2 grid grid-cols-2 gap-x-4 gap-y-1 moldy-ui-caption text-muted-foreground">
            <div className="flex items-center justify-between gap-2">
              <dt>{t('rootTools')}</dt>
              <dd className="tabular-nums text-foreground">{countText(summary.rootToolCalls)}</dd>
            </div>
            <div className="flex items-center justify-between gap-2">
              <dt>{t('descendantTools')}</dt>
              <dd className="tabular-nums text-foreground">
                {countText(summary.descendantToolCalls)}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-2">
              <dt>{t('rootSubagents')}</dt>
              <dd className="tabular-nums text-foreground">
                {countText(summary.rootSubagentCalls)}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-2">
              <dt>{t('descendantSubagents')}</dt>
              <dd className="tabular-nums text-foreground">
                {countText(summary.descendantSubagentCalls)}
              </dd>
            </div>
          </dl>
          <div className="mb-1.5 flex items-center gap-1.5 moldy-ui-caption font-medium text-foreground">
            <ListTreeIcon className="size-3.5" aria-hidden />
            {t('history')}
          </div>
          <ol className="space-y-1.5">
            {summary.activityTruncated ? (
              <li className="moldy-ui-micro text-muted-foreground">{t('truncated')}</li>
            ) : null}
            {summary.activities.map((activity) => {
              const activityElapsed = formatRunDuration(activity.elapsedMs, t)
              return (
                <li
                  key={activity.key}
                  className="flex min-w-0 items-start justify-between gap-3 moldy-ui-caption"
                  data-activity-kind={activity.kind}
                >
                  <span className="min-w-0 truncate text-foreground">
                    {activityName(activity)}
                    {activity.namespace.length > 0 ? (
                      <span className="ml-1.5 text-muted-foreground">
                        {activity.namespace.join(' / ')}
                      </span>
                    ) : null}
                  </span>
                  {activityElapsed ? (
                    <span className="shrink-0 tabular-nums text-muted-foreground">
                      {t('activityElapsed', { value: activityElapsed })}
                    </span>
                  ) : null}
                </li>
              )
            })}
          </ol>
        </div>
      ) : null}
    </section>
  )
}

export function MessageRunSummary() {
  const conversationId = useChatConversationId()
  const messageId = useAuiState((state) => state.message?.id)
  // The link query is shared by the visible-ID batch. A thread-wide phase keeps
  // historical summaries on the same lifecycle and flips only after finalization begins.
  const threadIsRunning = useAuiState((state) => state.thread.isRunning)
  const messageIdsSignature = useAuiState((state) =>
    state.thread.messages.map((message) => message.id).join(','),
  )
  const visibleMessageIds = messageIdsSignature ? messageIdsSignature.split(',') : []
  const { summary } = useMessageRunSummary(
    conversationId,
    messageId,
    visibleMessageIds,
    threadIsRunning,
  )
  return summary ? <RunSummaryPanel summary={summary} /> : null
}
