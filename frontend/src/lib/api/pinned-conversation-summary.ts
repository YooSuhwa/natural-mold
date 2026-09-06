import { apiFetch } from './client'
import type {
  PinnedConversationSummary,
  PinnedConversationSummaryEnvelope,
} from '@/lib/types/pinned-conversation-summary'

export const pinnedConversationSummaryApi = {
  get: (conversationId: string) =>
    apiFetch<PinnedConversationSummaryEnvelope>(
      `/api/conversations/${conversationId}/pinned-summary`,
    ),
  pin: (conversationId: string, messageId: string) =>
    apiFetch<PinnedConversationSummary>(`/api/conversations/${conversationId}/pinned-summary`, {
      method: 'PUT',
      body: JSON.stringify({ message_id: messageId }),
    }),
  unpin: (conversationId: string) =>
    apiFetch<void>(`/api/conversations/${conversationId}/pinned-summary`, {
      method: 'DELETE',
    }),
}
