import type { ThreadAssistantMessage } from '@assistant-ui/react'
import type { AgUiActivitySnapshot, AgUiMessageLink } from './chat-history-types'

export function isBoundAgUiDataCard(data: Record<string, unknown>): boolean {
  return data.tool_call_id != null || data.public_tool_call_id != null
}

export function matchesAgUiDataCardCall(
  data: Record<string, unknown>,
  toolCallId: string,
  verifiedIssuerIds: ReadonlySet<string>,
  parentMessageId?: string,
): boolean {
  if (!toolCallId || data.public_tool_call_id !== toolCallId) return false
  return [parentMessageId, data.message_id, data.public_message_id].every(
    (claim) => claim == null || (typeof claim === 'string' && verifiedIssuerIds.has(claim)),
  )
}

export function indexAgUiToolCards(activities: readonly AgUiActivitySnapshot[]) {
  const index = new Map<string, AgUiActivitySnapshot[]>()
  for (const activity of activities) {
    const call = activity.content.public_tool_call_id
    if (activity.activityType !== 'moldy.ui_data' || typeof call !== 'string' || !call) continue
    const cards = index.get(call)
    if (cards) cards.push(activity)
    else index.set(call, [activity])
  }
  return index
}

export function toolCardsForMessage(
  message: ThreadAssistantMessage,
  index: ReadonlyMap<string, readonly AgUiActivitySnapshot[]>,
  links: readonly AgUiMessageLink[],
): AgUiActivitySnapshot[] {
  const custom = message.metadata.custom
  const agui = custom.agui
  const wire =
    agui && typeof agui === 'object' && 'messageId' in agui && typeof agui.messageId === 'string'
      ? agui.messageId
      : message.id
  const child =
    agui && typeof agui === 'object' && 'name' in agui && typeof agui.name === 'string'
      ? message.id
      : undefined
  const calls = new Map<
    string,
    Extract<ThreadAssistantMessage['content'][number], { type: 'tool-call' }> | null
  >()
  for (const part of message.content) {
    if (part.type === 'tool-call' && index.has(part.toolCallId)) {
      calls.set(part.toolCallId, calls.has(part.toolCallId) ? null : part)
    }
  }
  const cards: AgUiActivitySnapshot[] = []
  for (const call of calls.values()) {
    if (!call) continue
    const issuers = new Set(call.parentId ? [call.parentId] : [wire, message.id])
    if (!call.parentId && typeof custom.publicMessageId === 'string')
      issuers.add(custom.publicMessageId)
    const matches = links.filter(
      (link) =>
        link.subagentRunId === child &&
        (issuers.has(link.messageId) ||
          (link.uiMessageId !== null && issuers.has(link.uiMessageId))),
    )
    const exact = matches.filter(
      (link) => link.uiMessageId != null && link.uiMessageId === custom.publicMessageId,
    )
    const selected = exact.length ? exact : matches
    if (selected.length > 1) continue
    const link = selected[0]
    if (link) {
      issuers.add(link.messageId)
      if (link.uiMessageId) issuers.add(link.uiMessageId)
    }
    for (const activity of index.get(call.toolCallId) ?? []) {
      if (activity.subagentRunId !== child || !activity.physicalRunId) continue
      if (link?.physicalRunId && link.physicalRunId !== activity.physicalRunId) continue
      if (activity.parentToolCallId && activity.parentToolCallId !== call.toolCallId) continue
      if (
        matchesAgUiDataCardCall(
          activity.content,
          call.toolCallId,
          issuers,
          activity.parentMessageId,
        )
      ) {
        cards.push(activity)
      }
    }
  }
  return cards
}
