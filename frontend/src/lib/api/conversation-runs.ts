import { apiFetch } from './client'
import type { ConversationRun } from '@/lib/types'

export const conversationRunsApi = {
  active: (conversationId: string) =>
    apiFetch<ConversationRun | null>(`/api/conversations/${conversationId}/runs/active`),
  get: (conversationId: string, runId: string) =>
    apiFetch<ConversationRun>(`/api/conversations/${conversationId}/runs/${runId}`),
  cancel: (conversationId: string, runId: string) =>
    apiFetch<ConversationRun>(`/api/conversations/${conversationId}/runs/${runId}/cancel`, {
      method: 'POST',
    }),
}
