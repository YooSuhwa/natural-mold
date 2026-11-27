import { isMcpAppUuid } from '@/lib/chat/mcp-apps/metadata'
import type { AgUiActivitySnapshot, AgUiMessageLink } from './chat-history-types'

export interface WidgetBinding {
  readonly runId: string
  readonly bindingId: string
  readonly toolCallId: string
  readonly publicToolCallId: string
}

export interface WidgetToolOwner {
  readonly messageId: string
  readonly wireMessageId?: string
  readonly publicMessageId?: string
  readonly toolParentId?: string
  readonly toolCallId: string
}

function matchesMessage(link: AgUiMessageLink, ids: ReadonlySet<string>): boolean {
  return ids.has(link.messageId) || (link.uiMessageId !== null && ids.has(link.uiMessageId))
}

function isToolId(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length > 0 &&
    value.length <= 255 &&
    value !== '.' &&
    value !== '..' &&
    !/[\p{Cc}\p{Cs}]/u.test(value)
  )
}

/** Bind to the actual assistant tool part. The SDK can append a late activity to a later bubble. */
export function findWidgetBinding(
  owner: WidgetToolOwner,
  activities: readonly AgUiActivitySnapshot[],
  links: readonly AgUiMessageLink[],
): WidgetBinding | null {
  if (!isToolId(owner.toolCallId)) return null
  const messageIds = new Set([owner.messageId])
  if (owner.wireMessageId) messageIds.add(owner.wireMessageId)
  if (owner.publicMessageId) messageIds.add(owner.publicMessageId)
  const ownerIds = owner.toolParentId ? new Set([owner.toolParentId]) : messageIds
  const candidates = links.filter((link) => matchesMessage(link, ownerIds))
  const exactBubble = candidates.filter(
    (link) => link.uiMessageId === owner.publicMessageId || link.uiMessageId === owner.messageId,
  )
  const selected = exactBubble.length ? exactBubble : candidates
  if (selected.length > 1) return null
  const link = selected[0]
  if (link) {
    ownerIds.add(link.messageId)
    if (link.uiMessageId) ownerIds.add(link.uiMessageId)
  }

  let match: WidgetBinding | null = null
  for (const activity of activities) {
    if (activity.activityType !== 'moldy.mcp_app') continue
    const data = activity.content
    // Subagent streams use a lane-specific public ID. The binding route uses the original ID.
    const publicToolCallId =
      data.public_tool_call_id === undefined ? data.tool_call_id : data.public_tool_call_id
    if (publicToolCallId !== owner.toolCallId || !isToolId(data.tool_call_id)) continue
    if (!isMcpAppUuid(data.run_id) || !isMcpAppUuid(data.binding_id)) continue
    if (activity.physicalRunId && activity.physicalRunId !== data.run_id) continue
    if (link?.physicalRunId && link.physicalRunId !== data.run_id) continue
    if (
      activity.subagentRunId &&
      activity.subagentRunId !== owner.messageId &&
      activity.subagentRunId !== link?.subagentRunId
    )
      continue
    if (link?.subagentRunId && link.subagentRunId !== activity.subagentRunId) continue
    const publicOwner = data.public_message_id
    if (
      publicOwner !== undefined &&
      (typeof publicOwner !== 'string' || !ownerIds.has(publicOwner))
    )
      continue
    if (activity.parentMessageId && !ownerIds.has(activity.parentMessageId)) continue
    if (!activity.parentMessageId && !publicOwner) continue
    if (
      match &&
      (match.bindingId !== data.binding_id ||
        match.runId !== data.run_id ||
        match.toolCallId !== data.tool_call_id)
    )
      return null
    match = {
      runId: data.run_id,
      bindingId: data.binding_id,
      toolCallId: data.tool_call_id,
      publicToolCallId,
    }
  }
  return match
}
