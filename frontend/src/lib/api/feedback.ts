import { apiFetch } from './client'
import type { MessageFeedbackRow } from '@/lib/types'

export const feedbackApi = {
  /** Upsert (or replace) the current user's rating for a message. */
  set: (messageId: string, rating: 'up' | 'down', conversationId: string) =>
    apiFetch<MessageFeedbackRow>(`/api/messages/${messageId}/feedback`, {
      method: 'POST',
      body: JSON.stringify({ rating, conversation_id: conversationId }),
    }),
  /** Toggle off — clear the current user's rating for a message. */
  clear: (messageId: string) =>
    apiFetch<void>(`/api/messages/${messageId}/feedback`, {
      method: 'DELETE',
    }),
  /** All ratings the current user has set inside one conversation. */
  listForConversation: (conversationId: string) =>
    apiFetch<MessageFeedbackRow[]>(`/api/conversations/${conversationId}/feedback`),
}
