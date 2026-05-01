import { apiFetch } from './client'
import type {
  Conversation,
  ConversationUpdateRequest,
  Message,
  MessagesEnvelope,
} from '@/lib/types'

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
  /**
   * M-CHAT1b — backend now returns `MessagesEnvelope`. We unwrap to keep the
   * existing `Message[]` consumer signature, but expose `messagesEnvelope` for
   * callers that need the active-branch metadata.
   */
  messages: (conversationId: string): Promise<Message[]> =>
    apiFetch<MessagesEnvelope>(`/api/conversations/${conversationId}/messages`).then(
      (env) => env.messages,
    ),
  messagesEnvelope: (conversationId: string) =>
    apiFetch<MessagesEnvelope>(`/api/conversations/${conversationId}/messages`),
  /**
   * M-CHAT1b — record the user-selected branch tip so subsequent
   * edits/regenerates fork off it.
   */
  switchBranch: (conversationId: string, checkpointId: string) =>
    apiFetch<void>(`/api/conversations/${conversationId}/messages/switch-branch`, {
      method: 'POST',
      body: JSON.stringify({ checkpoint_id: checkpointId }),
    }),
}
