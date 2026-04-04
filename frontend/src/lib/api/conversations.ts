import { apiFetch } from './client'
import type { Conversation, ConversationUpdateRequest, Message } from '@/lib/types'

export const conversationsApi = {
  list: (agentId: string) => apiFetch<Conversation[]>(`/api/agents/${agentId}/conversations`),
  create: (agentId: string, title?: string) =>
    apiFetch<Conversation>(`/api/agents/${agentId}/conversations`, {
      method: 'POST',
      body: JSON.stringify({ title }),
    }),
  update: (conversationId: string, data: ConversationUpdateRequest) =>
    apiFetch<Conversation>(`/api/conversations/${conversationId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  delete: (conversationId: string) =>
    apiFetch<void>(`/api/conversations/${conversationId}`, {
      method: 'DELETE',
    }),
  messages: (conversationId: string) =>
    apiFetch<Message[]>(`/api/conversations/${conversationId}/messages`),
}
