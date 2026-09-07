import type { RunActivity } from '@/lib/chat/langgraph-runtime/activity-model'
import type {
  ConversationRunActivityMetric,
  ConversationRunMetrics,
  ConversationRunStatus,
} from '@/lib/types'
import { sourceMessageIdFromThreadMessageId } from '@/lib/chat/langgraph-runtime/message-list'

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const TERMINAL_MESSAGE_PREFIXES = [
  'moldy-canceled-',
  'moldy-canceling-',
  'moldy-stale-',
  'moldy-failed-',
  'canceled-',
] as const
const MAX_RUN_MESSAGE_KEY_LENGTH = 512

function durableMessageKey(messageId: string | null | undefined): string | null {
  const sourceId = sourceMessageIdFromThreadMessageId(messageId)
  if (!sourceId || sourceId.length > MAX_RUN_MESSAGE_KEY_LENGTH) return null
  return runIdFromMessageId(sourceId) ? null : sourceId
}

export interface RunSummaryActivity {
  readonly key: string
  readonly kind: string
  readonly namespace: readonly string[]
  readonly callId: string | null
  readonly name: string | null
  readonly elapsedMs: number | null
  readonly status?: RunActivity['status']
}

export interface RunSummary {
  readonly runId: string
  readonly status: ConversationRunStatus
  readonly elapsedMs: number | null
  readonly startedAtMs: number | null
  readonly isLive: boolean
  readonly rootToolCalls: number | null
  readonly descendantToolCalls: number | null
  readonly rootSubagentCalls: number | null
  readonly descendantSubagentCalls: number | null
  readonly activities: readonly RunSummaryActivity[]
  readonly activityTruncated: boolean
}

export interface RunMessageLink {
  readonly message_id: string
  readonly run_id: string
}

function persistedActivity(
  activity: ConversationRunActivityMetric,
  index: number,
): RunSummaryActivity {
  return {
    key: `${index}:${activity.call_id ?? activity.kind}`,
    kind: activity.kind,
    namespace: activity.namespace,
    callId: activity.call_id,
    name: activity.name,
    elapsedMs: activity.elapsed_ms,
  }
}

export function buildPersistedRunSummary(
  runId: string,
  status: ConversationRunStatus,
  metrics: ConversationRunMetrics | null,
): RunSummary {
  return {
    runId,
    status,
    elapsedMs: metrics?.elapsed_ms ?? null,
    startedAtMs: null,
    isLive: false,
    rootToolCalls: metrics?.root_tool_calls ?? null,
    descendantToolCalls: metrics?.descendant_tool_calls ?? null,
    rootSubagentCalls: metrics?.root_subagent_calls ?? null,
    descendantSubagentCalls: metrics?.descendant_subagent_calls ?? null,
    activities: metrics?.activity_json.map(persistedActivity) ?? [],
    activityTruncated: metrics?.activity_truncated ?? false,
  }
}

function stableCount(
  activities: readonly RunActivity[],
  kinds: readonly RunActivity['kind'][],
  descendant: boolean,
): number {
  const keys = new Set<string>()
  for (const activity of activities) {
    if (!kinds.includes(activity.kind)) continue
    if (activity.namespace.length > 0 !== descendant) continue
    keys.add(activity.toolCallId ?? activity.id)
  }
  return keys.size
}

function validTimestamp(value: string | undefined): number | null {
  if (!value) return null
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? timestamp : null
}

function liveElapsedMs(
  activities: readonly RunActivity[],
  nowMs: number,
): [number | null, number | null] {
  const starts = activities
    .map((activity) => validTimestamp(activity.startedAt))
    .filter((value): value is number => value !== null)
  if (starts.length === 0) return [null, null]
  const startedAtMs = Math.min(...starts)
  const isRunning = activities.some(
    (activity) => activity.status === 'running' || activity.status === 'pending',
  )
  const ends = activities
    .map((activity) => validTimestamp(activity.endedAt))
    .filter((value): value is number => value !== null)
  const endMs = isRunning || ends.length === 0 ? nowMs : Math.max(...ends)
  return [Math.max(0, endMs - startedAtMs), startedAtMs]
}

function liveActivity(activity: RunActivity, runStartedAtMs: number | null): RunSummaryActivity {
  const startedAt = validTimestamp(activity.startedAt)
  return {
    key: activity.id,
    kind: activity.kind,
    namespace: activity.namespace,
    callId: activity.toolCallId ?? null,
    name: activity.title || null,
    elapsedMs:
      startedAt !== null && runStartedAtMs !== null
        ? Math.max(0, startedAt - runStartedAtMs)
        : null,
    status: activity.status,
  }
}

export function buildLiveRunSummary(
  activities: readonly RunActivity[],
  nowMs = Date.now(),
): RunSummary | null {
  const runId = activities.at(-1)?.runId
  if (!runId) return null
  const current = activities.filter((activity) => activity.runId === runId)
  const [elapsedMs, startedAtMs] = liveElapsedMs(current, nowMs)
  return {
    runId,
    status: 'running',
    elapsedMs,
    startedAtMs,
    isLive: current.some(
      (activity) => activity.status === 'running' || activity.status === 'pending',
    ),
    rootToolCalls: stableCount(current, ['tool'], false),
    descendantToolCalls: stableCount(current, ['tool'], true),
    rootSubagentCalls: stableCount(current, ['subagent', 'background_subagent'], false),
    descendantSubagentCalls: stableCount(current, ['subagent', 'background_subagent'], true),
    activities: current.map((activity) => liveActivity(activity, startedAtMs)),
    activityTruncated: false,
  }
}

export function runIdFromMessageId(messageId: string | null | undefined): string | null {
  if (!messageId) return null
  for (const prefix of TERMINAL_MESSAGE_PREFIXES) {
    if (!messageId.startsWith(prefix)) continue
    const candidate = messageId.slice(prefix.length)
    return UUID_PATTERN.test(candidate) ? candidate : null
  }
  return null
}

export function runIdForMessage(
  links: readonly RunMessageLink[],
  messageId: string | null | undefined,
): string | null {
  const terminalRunId = runIdFromMessageId(messageId)
  if (terminalRunId) return terminalRunId
  const messageKey = durableMessageKey(messageId)
  if (!messageKey) return null
  const link = links.find((item) => item.message_id === messageKey)
  return link && UUID_PATTERN.test(link.run_id) ? link.run_id : null
}

export function messageIdBatchFor(
  messageId: string | null | undefined,
  visibleMessageIds: readonly string[],
): readonly string[] {
  const messageKey = durableMessageKey(messageId)
  if (!messageKey) return []
  const ids = [
    ...new Set(
      [...visibleMessageIds, messageKey]
        .map((id) => durableMessageKey(id))
        .filter((id): id is string => id !== null),
    ),
  ].sort()
  const index = ids.indexOf(messageKey)
  const start = Math.floor(index / 50) * 50
  return ids.slice(start, start + 50)
}
