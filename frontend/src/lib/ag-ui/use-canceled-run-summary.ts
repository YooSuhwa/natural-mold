'use client'

import { buildPersistedRunSummary } from '@/lib/chat/run-summary-model'
import { useConversationRun } from '@/lib/hooks/use-conversation-runs'
import type { ConversationRun } from '@/lib/types'
import type { AgUiChatSessionState } from './chat-history'

/** Stopping before the first assistant frame leaves no message to host the
 * persisted summary. Anchor it to the branch-confirmed physical run instead. */
export function useCanceledRunSummary(
  conversationId: string,
  state: AgUiChatSessionState,
  latest: ConversationRun | null,
) {
  const id = state.activeRunId || state.readDenied ? null : state.lastTerminalPhysicalRunId
  const declared =
    (latest?.id === id &&
      latest?.conversation_id === conversationId &&
      latest.status === 'canceled') ||
    state.nativeActivities.some(
      (item) => item.runId === id && item.terminalRunStatus === 'canceled',
    )
  // A message panel may have cached this run while it was still running.
  // Refresh its scoped GET as soon as cancellation is confirmed on this branch.
  const detail = useConversationRun(conversationId, id, !!id && declared, { staleTime: 0 })
  if (!id || !declared || detail.isError) return null
  const run = detail.data
  if (
    !run ||
    run.id !== id ||
    run.conversation_id !== conversationId ||
    run.status !== 'canceled'
  ) {
    return null
  }
  return buildPersistedRunSummary(run.id, run.status, run.metrics, run.error_code)
}
