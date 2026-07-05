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
  FileItem,
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
  createDraft: (agentId: string) =>
    apiFetch<Conversation>(`/api/agents/${agentId}/conversations/draft`, {
      method: 'POST',
      body: JSON.stringify({}),
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
  /**
   * 대화 파일 목록(생성 산출물 + 사용자 첨부)을 합쳐 반환. 우측 레일의
   * 첨부 섹션은 `source==='attached'`만 사용하고, 생성 산출물은 기존
   * `chatArtifactsAtom` 스트리밍 경로를 그대로 유지한다.
   */
  files: (conversationId: string): Promise<FileItem[]> =>
    apiFetch<FileItem[]>(`/api/conversations/${conversationId}/files`),
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
  /**
   * Follow-up 고스트 제안 1개 생성 — 런 종료 시 호출. system LLM 미설정 등
   * 어떤 이유로든 생성 불가면 suggestion=null(고스트 미표시).
   */
  followupSuggestion: (conversationId: string) =>
    apiFetch<{ suggestion: string | null }>(
      `/api/conversations/${conversationId}/followup-suggestion`,
      { method: 'POST' },
    ),
}
