'use client'

import { useLayoutEffect, useMemo, useRef } from 'react'
import type { FeedbackAdapter } from '@assistant-ui/react'
import { feedbackApi } from '@/lib/api/feedback'
import { sourceMessageIdFromThreadMessageId } from './langgraph-runtime/message-list'
import { reportClientError } from '@/lib/logging/client-logger'
import { currentOrgScope, isOrgScopeCurrent } from '@/lib/org-scope/org-scope-signal'
import type { Message } from '@/lib/types'

type Rating = 'up' | 'down'

/** A public feedback target read from a freshly fetched messages list. */
export interface RefreshedFeedbackTarget {
  readonly id: string
  readonly rating: Rating | undefined
}

/**
 * Wire the assistant-ui FeedbackAdapter to ``POST/DELETE /api/messages/{id}/feedback``.
 *
 * Toggle behaviour: re-clicking the active rating clears it. assistant-ui
 * doesn't expose the prior rating to ``submit`` directly, so the caller hands
 * us a ``getActiveRating(messageId)`` lookup over the latest message store.
 */
export function useChatFeedbackAdapter(
  conversationId: string | undefined,
  getActiveRating: (messageId: string) => Rating | undefined,
  options?: {
    readonly onMutate?: () => void
    readonly resolveMessageId?: (messageId: string) => string | undefined
    readonly refreshMessageId?: (messageId: string) => Promise<RefreshedFeedbackTarget | undefined>
  },
): FeedbackAdapter | undefined {
  const { onMutate, resolveMessageId, refreshMessageId } = options ?? {}
  const conversationScope = useMemo(() => ({ conversationId }), [conversationId])
  const current = useRef<typeof conversationScope | null>(null)
  useLayoutEffect(() => {
    current.current = conversationScope
    return () => {
      current.current = null
    }
  }, [conversationScope])
  return useMemo(() => {
    const targetConversationId = conversationScope.conversationId
    if (!targetConversationId) return undefined
    return {
      submit: ({ message, type }) => {
        if (current.current !== conversationScope) return
        const scope = currentOrgScope()
        const next: Rating = type === 'positive' ? 'up' : 'down'
        const persist = (messageId: string, currentRating: Rating | undefined) => {
          if (current.current !== conversationScope || !isOrgScopeCurrent(scope)) return
          const promise =
            currentRating === next
              ? feedbackApi.clear(messageId)
              : feedbackApi.set(messageId, next, targetConversationId)
          // Fire-and-forget — assistant-ui already updates local UI
          // optimistically via metadata.submittedFeedback. We just persist.
          promise
            .then(() => onMutate?.())
            .catch((err) => {
              reportClientError('feedback', 'submit failed:', err)
            })
        }
        const messageId = resolveMessageId ? resolveMessageId(message.id) : message.id
        if (messageId) {
          persist(messageId, getActiveRating(messageId))
          return
        }
        // A reply that just finished is not in the messages query yet, and the server
        // would 404 its runtime id. Refresh the query once, then persist; dropping the
        // click here would lose the rating silently.
        if (!refreshMessageId) return
        refreshMessageId(message.id)
          .then((target) => {
            if (target) persist(target.id, target.rating)
          })
          .catch((err) => {
            reportClientError('feedback', 'submit failed:', err)
          })
      },
    }
  }, [conversationScope, getActiveRating, onMutate, resolveMessageId, refreshMessageId])
}

/** Map every runtime and public message id to the public id feedback is stored under. */
export function publicMessageIdMap(messages: readonly Message[]): Map<string, string> {
  const map = new Map<string, string>()
  for (const message of messages) {
    map.set(message.id, message.id)
    if (message.runtime_message_id) map.set(message.runtime_message_id, message.id)
  }
  return map
}

export function resolveFeedbackMessageId(
  publicIds: ReadonlyMap<string, string>,
  messageId: string,
): string | undefined {
  const sourceId = sourceMessageIdFromThreadMessageId(messageId)
  return publicIds.get(messageId) ?? (sourceId ? publicIds.get(sourceId) : undefined)
}
