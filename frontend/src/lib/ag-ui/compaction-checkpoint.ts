import { apiFetch } from '@/lib/api/client'
import type { CompactionEvidenceTarget } from '@/lib/api/conversation-history-evidence'

/** A live summary gets its checkpoint from the assistant turn that owns its saved marker. */
export async function resolveAgUiCompactionCheckpoint(
  target: CompactionEvidenceTarget,
  signal: AbortSignal,
): Promise<string> {
  if (!target.messageId) throw new Error('history_evidence_unavailable')

  const path = `/api/conversations/${encodeURIComponent(target.conversationId)}/ag-ui/snapshot`
  const [raw, { parseWireChatSnapshot }] = await Promise.all([
    apiFetch<unknown>(path, { signal, cache: 'no-store' }),
    import('./chat-history-types'),
  ])
  signal.throwIfAborted()
  const snapshot = parseWireChatSnapshot(raw, target.conversationId)
  const markers = snapshot.activities.filter(
    (activity) =>
      activity.activityType === 'moldy.compaction' &&
      (target.activityId === undefined || activity.messageId === target.activityId) &&
      activity.content.state === 'done' &&
      !activity.subagentRunId &&
      activity.parentMessageId === target.messageId &&
      activity.content.history_id === target.historyId &&
      activity.content.cutoff_index === target.cutoffIndex,
  )
  const owner = snapshot.messageLinks.find((link) => link.messageId === target.messageId)
  const isAssistant = snapshot.messages.some(
    (message) =>
      message.id === target.messageId && message.role === 'assistant' && !message.subagentRunId,
  )
  if (
    markers.length !== 1 ||
    !isAssistant ||
    !owner?.physicalRunId ||
    owner.subagentRunId ||
    owner.physicalRunId !== markers[0].physicalRunId ||
    !owner.checkpointId ||
    owner.checkpointId.startsWith('pending-') ||
    !/^[A-Za-z0-9_-]{1,64}$/.test(owner.checkpointId)
  ) {
    throw new Error('history_evidence_unavailable')
  }
  return owner.checkpointId
}
