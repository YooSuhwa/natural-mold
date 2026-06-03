'use client'

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { conversationsApi } from '@/lib/api/conversations'
import type { Conversation, ConversationPageParams, ConversationUpdateRequest } from '@/lib/types'

export const conversationKeys = {
  list: (agentId: string) => ['agents', agentId, 'conversations'] as const,
  pages: (agentId: string, params: Omit<ConversationPageParams, 'cursor'>) =>
    ['agents', agentId, 'conversations', 'page', params] as const,
  messages: (conversationId: string) => ['conversations', conversationId, 'messages'] as const,
  debugTraces: (conversationId: string) =>
    ['conversations', conversationId, 'debug-traces'] as const,
  debugTraceDetail: (conversationId: string, traceId: string) =>
    ['conversations', conversationId, 'debug-traces', traceId] as const,
}

export function useConversations(agentId: string) {
  return useQuery({
    queryKey: conversationKeys.list(agentId),
    queryFn: () => conversationsApi.list(agentId),
    enabled: !!agentId,
  })
}

export function useConversationPages(
  agentId: string,
  params: Omit<ConversationPageParams, 'cursor'> = {},
) {
  const pageParams = {
    limit: params.limit ?? 30,
    q: params.q?.trim() || undefined,
  }
  return useInfiniteQuery({
    queryKey: conversationKeys.pages(agentId, pageParams),
    queryFn: ({ pageParam }) =>
      conversationsApi.page(agentId, {
        ...pageParams,
        cursor: pageParam,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: !!agentId,
  })
}

export function useMessages(conversationId: string) {
  return useQuery({
    queryKey: conversationKeys.messages(conversationId),
    // envelope 전체 fetch + select로 ``Message[]``만 노출 — caller 호환을
    // 유지하면서 ``useMessagesEnvelope``과 cache를 공유한다.
    queryFn: () => conversationsApi.messagesEnvelope(conversationId),
    select: (env) => env.messages,
    enabled: !!conversationId,
    refetchOnWindowFocus: false,
  })
}

/** W7-4 — Composer 토큰 바의 cost 표시를 위해 envelope 전체에 접근하는 hook.
 *  ``useMessages``와 동일한 queryKey/queryFn을 공유해 추가 fetch 비용 없음. */
export function useMessagesEnvelope(conversationId: string) {
  return useQuery({
    queryKey: conversationKeys.messages(conversationId),
    queryFn: () => conversationsApi.messagesEnvelope(conversationId),
    enabled: !!conversationId,
    refetchOnWindowFocus: false,
  })
}

export function useConversationDebugTraces(conversationId: string, enabled = true) {
  return useQuery({
    queryKey: conversationKeys.debugTraces(conversationId),
    queryFn: () => conversationsApi.debugTraces(conversationId),
    enabled: enabled && !!conversationId,
    refetchOnWindowFocus: false,
  })
}

export function useConversationDebugTraceDetail(
  conversationId: string,
  traceId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: conversationKeys.debugTraceDetail(conversationId, traceId ?? 'none'),
    queryFn: () => conversationsApi.debugTraceDetail(conversationId, traceId ?? ''),
    enabled: enabled && !!conversationId && !!traceId,
    refetchOnWindowFocus: false,
  })
}

export function useCreateConversation(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (title?: string) => conversationsApi.create(agentId, title),
    onSuccess: () => qc.invalidateQueries({ queryKey: conversationKeys.list(agentId) }),
  })
}

export function useUpdateConversation(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ConversationUpdateRequest }) =>
      conversationsApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: conversationKeys.list(agentId) }),
  })
}

export function useDeleteConversation(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => conversationsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: conversationKeys.list(agentId) }),
  })
}

export function useMarkConversationRead(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (conversationId: string) => conversationsApi.markRead(conversationId),
    onSuccess: (conversation) => {
      qc.setQueryData<Conversation[]>(conversationKeys.list(agentId), (current) =>
        current?.map((item) => (item.id === conversation.id ? { ...item, ...conversation } : item)),
      )
      qc.invalidateQueries({ queryKey: conversationKeys.list(agentId), refetchType: 'inactive' })
      qc.invalidateQueries({ queryKey: ['agents'] })
      qc.invalidateQueries({ queryKey: ['triggers'] })
      qc.invalidateQueries({ queryKey: ['triggers', 'summary'] })
    },
  })
}
