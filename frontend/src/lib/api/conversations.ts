import { apiFetch } from './client'
import type {
  Conversation,
  ConversationListEnvelope,
  ConversationPageParams,
  ConversationUpdateRequest,
  ConversationWithAgent,
  ConversationWithAgentListEnvelope,
  DebugTraceDetailResponse,
  DebugTraceListResponse,
  Message,
  MessagesEnvelope,
} from '@/lib/types'

function buildConversationPageSearch(params?: ConversationPageParams): string {
  const search = new URLSearchParams()
  if (typeof params?.limit === 'number') search.set('limit', String(params.limit))
  if (params?.cursor) search.set('cursor', params.cursor)
  if (params?.q) search.set('q', params.q)
  if (params?.sort) search.set('sort', params.sort)
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

export const conversationsApi = {
  list: (agentId: string) => apiFetch<Conversation[]>(`/api/agents/${agentId}/conversations`),
  page: (agentId: string, params?: ConversationPageParams) =>
    apiFetch<ConversationListEnvelope>(
      `/api/agents/${agentId}/conversations/page${buildConversationPageSearch(params)}`,
    ),
  globalPage: (params?: ConversationPageParams) =>
    apiFetch<ConversationWithAgentListEnvelope>(
      `/api/conversations/page${buildConversationPageSearch(params)}`,
    ),
  get: (conversationId: string) =>
    apiFetch<ConversationWithAgent>(`/api/conversations/${conversationId}`),
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
  markRead: (conversationId: string) =>
    apiFetch<Conversation>(`/api/conversations/${conversationId}/read`, {
      method: 'POST',
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
  debugTraces: (conversationId: string) =>
    apiFetch<DebugTraceListResponse>(`/api/conversations/${conversationId}/debug/traces`),
  debugTraceDetail: (conversationId: string, traceId: string) =>
    apiFetch<DebugTraceDetailResponse>(
      `/api/conversations/${conversationId}/debug/traces/${traceId}`,
    ),
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
