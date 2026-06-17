'use client'

import { useMemo } from 'react'
import type { FeedbackAdapter } from '@assistant-ui/react'
import { feedbackApi } from '@/lib/api/feedback'
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
  onMutate?: () => void,
): FeedbackAdapter | undefined {
  return useMemo(() => {
    if (!conversationId) return undefined
    return {
      submit: ({ message, type }) => {
        const next: 'up' | 'down' = type === 'positive' ? 'up' : 'down'
        const current = getActiveRating(message.id)
        const promise =
          current === next
            ? feedbackApi.clear(message.id)
            : feedbackApi.set(message.id, next, conversationId)
        // Fire-and-forget — assistant-ui already updates local UI
        // optimistically via metadata.submittedFeedback. We just persist.
        promise
          .then(() => onMutate?.())
          .catch((err) => {
            reportClientError('feedback', 'submit failed:', err)
          })
      },
    }
  }, [conversationId, getActiveRating, onMutate])
}
