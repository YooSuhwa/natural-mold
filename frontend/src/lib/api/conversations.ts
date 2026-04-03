import { apiFetch } from './client'
import type { Conversation, Message } from '@/lib/types'

export const conversationsApi = {
  list: (agentId: string) => apiFetch<Conversation[]>(`/api/agents/${agentId}/conversations`),
  create: (agentId: string, title?: string) =>
    apiFetch<Conversation>(`/api/agents/${agentId}/conversations`, {
      method: 'POST',
      body: JSON.stringify({ title }),
    }),
  messages: (conversationId: string) =>
    apiFetch<Message[]>(`/api/conversations/${conversationId}/messages`),
}
