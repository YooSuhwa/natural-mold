import type { RunActivity } from '@/lib/chat/langgraph-runtime/activity-types'
import { reduceAgUiNativeActivities } from './activity-native-summary'
import type { AgUiHistoryChild } from './chat-history-children'
import type { AgUiMessageLink, WireChatSnapshot } from './chat-history-types'

function uniqueById<T>(items: readonly T[], getId: (item: T) => string) {
  const unique = new Map<string, T | null>()
  for (const item of items) {
    const key = getId(item)
    unique.set(key, unique.has(key) ? null : item)
  }
  return unique
}

function childrenInParentOrder(
  children: readonly AgUiHistoryChild[],
  selected: string,
  linkedLanes: ReadonlySet<string>,
): AgUiHistoryChild[] {
  const byId = uniqueById(children, (child) => child.subagentRunId)
  const admitted = new Map<string, number>()
  const ordered: AgUiHistoryChild[] = []
  function admit(child: AgUiHistoryChild, depth: number): boolean {
    if (depth > 16) return false
    const knownDepth = admitted.get(child.subagentRunId)
    if (knownDepth !== undefined) return knownDepth + depth - 1 <= 16
    if (
      child.physicalRunId ? child.physicalRunId !== selected : !linkedLanes.has(child.subagentRunId)
    ) {
      return false
    }
    let level = 1
    if (child.parentSubagentRunId) {
      const parent = byId.get(child.parentSubagentRunId)
      if (!parent || !admit(parent, depth + 1)) return false
      level += admitted.get(child.parentSubagentRunId) ?? 0
    }
    admitted.set(child.subagentRunId, level)
    ordered.push(child)
    return true
  }
  for (const child of byId.values()) {
    if (child) admit(child, 1)
  }
  return ordered
}

/** A retained message proves its call and output, but it cannot establish when
 * streaming ended or whether a response was in flight at snapshot time. */
export function restoreAgUiNativeActivities(snapshot: WireChatSnapshot): readonly RunActivity[] {
  const selected =
    snapshot.physicalRunId ??
    snapshot.latestPhysicalRunId ??
    snapshot.physicalInterruptRunId ??
    snapshot.failedPhysicalRunId
  if (!selected) return []

  const links = uniqueById(snapshot.messageLinks, (link) => link.messageId)
  const linked = uniqueById(snapshot.messages, (message) => message.id)
  const records: { message: WireChatSnapshot['messages'][number]; link: AgUiMessageLink }[] = []
  const linkedLanes = new Set<string>()
  const publicRunIds = new Set<string>([selected])
  for (const [id, message] of linked) {
    const link = links.get(id)
    if (
      !message ||
      link?.physicalRunId !== selected ||
      (link.subagentRunId ?? null) !== (message.subagentRunId ?? null)
    )
      continue
    records.push({ message, link })
    if (link.runId) publicRunIds.add(link.runId)
    if (link.subagentRunId) linkedLanes.add(link.subagentRunId)
  }
  let result: readonly RunActivity[] = []
  const apply = (event: Record<string, unknown>) => {
    result = reduceAgUiNativeActivities(result, event, selected)
  }
  const children = childrenInParentOrder(snapshot.subagents, selected, linkedLanes)
  const lanes = new Set(children.map((child) => child.subagentRunId))
  for (const child of children) {
    if (child.runId) publicRunIds.add(child.runId)
    apply({
      type: 'SUBAGENT_STARTED',
      subagentRunId: child.subagentRunId,
      name: child.name,
      parentSubagentRunId: child.parentSubagentRunId,
      parentToolCallId: child.parentToolCallId,
      parentMessageId: child.parentMessageId,
    })
  }
  const active = snapshot.physicalRunId === selected
  const selectedStatusKnown = snapshot.selectedPhysicalRunId
    ? snapshot.selectedPhysicalRunId === selected
    : snapshot.runId && publicRunIds.has(snapshot.runId)
  const status = selectedStatusKnown ? snapshot.runStatus : null
  const finished = !active && status === 'completed'
  const publicRecords = records.filter(
    ({ message }) => !message.subagentRunId || lanes.has(message.subagentRunId),
  )
  const calls = publicRecords.flatMap(({ message }) => {
    const lane = message.subagentRunId
    if (message.role !== 'assistant') return []
    if (finished && message.content?.length) {
      const identity = { messageId: message.id, subagentRunId: lane }
      apply({ type: 'TEXT_MESSAGE_START', role: 'assistant', ...identity })
      apply({ type: 'TEXT_MESSAGE_END', ...identity })
    }
    return (message.toolCalls ?? []).map((call) => ({
      key: JSON.stringify([lane ?? null, call.id]),
      toolCallId: call.id,
      toolCallName: call.function.name,
      parentMessageId: message.id,
      subagentRunId: lane,
    }))
  })
  const byLane = uniqueById(calls, (item) => item.key)
  const byIssuer = uniqueById(calls, (item) =>
    JSON.stringify([item.parentMessageId, item.toolCallId]),
  )
  const results = new Set<string>()
  for (const { message, link } of publicRecords) {
    if (
      message.role !== 'tool' ||
      (link.parentToolCallId && link.parentToolCallId !== message.toolCallId)
    )
      continue
    // A child can deliver its parent's tool result. In that case the snapshot's
    // verified issuing message, rather than the executor lane, identifies the call.
    const call = link.parentMessageId
      ? byIssuer.get(JSON.stringify([link.parentMessageId, message.toolCallId]))
      : byLane.get(JSON.stringify([message.subagentRunId ?? null, message.toolCallId]))
    if (call && byLane.get(call.key) === call) results.add(call.key)
  }
  for (const call of byLane.values()) {
    if (!call) continue
    apply({
      type: 'TOOL_CALL_START',
      toolCallId: call.toolCallId,
      toolCallName: call.toolCallName,
      parentMessageId: call.parentMessageId,
      subagentRunId: call.subagentRunId,
    })
    if (results.has(call.key)) {
      apply({
        type: 'TOOL_CALL_RESULT',
        toolCallId: call.toolCallId,
        subagentRunId: call.subagentRunId,
      })
    }
  }
  for (const child of [...children].reverse()) {
    const subagentRunId = child.subagentRunId
    switch (child.status) {
      case 'failed':
      case 'cancelled':
        apply({
          type: 'SUBAGENT_ERROR',
          subagentRunId,
          ...(child.status === 'cancelled' && { code: 'CANCELLED' }),
        })
        break
      case 'interrupted':
        apply({
          type: 'SUBAGENT_FINISHED',
          subagentRunId,
          outcome: { type: 'suspended', interruptIds: child.interruptIds ?? [] },
        })
        break
      case 'completed':
        apply({ type: 'SUBAGENT_FINISHED', subagentRunId })
        break
    }
  }
  if (active) return result
  const interrupts =
    snapshot.physicalInterruptRunId === selected
      ? snapshot.interrupts
          .filter((item) => !item.subagentRunId || lanes.has(item.subagentRunId))
          .map(({ id, toolCallId, subagentRunId }) => ({ id, toolCallId, subagentRunId }))
      : []
  if (interrupts.length || status === 'interrupted') {
    apply({ type: 'RUN_FINISHED', outcome: { type: 'interrupt', interrupts } })
  } else if (
    snapshot.failedPhysicalRunId === selected ||
    status === 'stale' ||
    status === 'failed'
  ) {
    apply({ type: 'RUN_ERROR' })
    if (status === 'stale') {
      result = result.map((entry) =>
        entry.terminalRunStatus === 'failed' ? { ...entry, terminalRunStatus: 'stale' } : entry,
      )
    }
  } else if (finished || status === 'canceled') {
    apply({ type: 'RUN_FINISHED', outcome: { type: finished ? 'success' : 'cancelled' } })
  }
  return result
}
