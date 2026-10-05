'use client'

import { useMemo } from 'react'
import type { FeedbackAdapter } from '@assistant-ui/react'
import { feedbackApi } from '@/lib/api/feedback'
import { sourceMessageIdFromThreadMessageId } from './langgraph-runtime/message-list'
import { reportClientError } from '@/lib/logging/client-logger'

/**
 * Wire the assistant-ui FeedbackAdapter to ``POST/DELETE /api/messages/{id}/feedback``.
 *
 * Toggle behaviour: re-clicking the active rating clears it. assistant-ui
 * doesn't expose the prior rating to ``submit`` directly, so the caller hands
 * us a ``getActiveRating(messageId)`` lookup over the latest message store.
 */
export function useChatFeedbackAdapter(
  conversationId: string | undefined,
  getActiveRating: (messageId: string) => 'up' | 'down' | undefined,
  options?: {
    readonly onMutate?: () => void
    readonly resolveMessageId?: (messageId: string) => string
  },
): FeedbackAdapter | undefined {
  const { onMutate, resolveMessageId } = options ?? {}
  return useMemo(() => {
    if (!conversationId) return undefined
    return {
      submit: ({ message, type }) => {
        const next: 'up' | 'down' = type === 'positive' ? 'up' : 'down'
        const messageId = resolveMessageId?.(message.id) ?? message.id
        const current = getActiveRating(messageId)
        const promise =
          current === next
            ? feedbackApi.clear(messageId)
            : feedbackApi.set(messageId, next, conversationId)
        // Fire-and-forget — assistant-ui already updates local UI
        // optimistically via metadata.submittedFeedback. We just persist.
        promise
          .then(() => onMutate?.())
          .catch((err) => {
            reportClientError('feedback', 'submit failed:', err)
          })
      },
    }
  }, [conversationId, getActiveRating, onMutate, resolveMessageId])
}

export function resolveFeedbackMessageId(
  publicIds: ReadonlyMap<string, string>,
  messageId: string,
): string {
  const sourceId = sourceMessageIdFromThreadMessageId(messageId)
  return publicIds.get(messageId) ?? (sourceId ? publicIds.get(sourceId) : undefined) ?? messageId
}
