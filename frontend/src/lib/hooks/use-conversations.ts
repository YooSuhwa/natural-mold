'use client'

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { conversationsApi } from '@/lib/api/conversations'
import { conversationPagesContainActiveRun } from '@/lib/chat-runs/status'
import type { Conversation, ConversationPageParams, ConversationUpdateRequest } from '@/lib/types'

interface ConversationPagesOptions {
  readonly enabled?: boolean
}

function normalizeConversationPageParams(
  params: Omit<ConversationPageParams, 'cursor'> = {},
): Omit<ConversationPageParams, 'cursor'> {
  return {
    limit: params.limit ?? 30,
    q: params.q?.trim() || undefined,
    sort: params.sort ?? 'updated',
  }
}

export const conversationKeys = {
  list: (agentId: string) => ['agents', agentId, 'conversations'] as const,
  pages: (agentId: string, params: Omit<ConversationPageParams, 'cursor'>) =>
    ['agents', agentId, 'conversations', 'page', params] as const,
  globalPages: (params: Omit<ConversationPageParams, 'cursor'>) =>
    ['conversations', 'page', params] as const,
  detail: (conversationId: string) => ['conversations', conversationId, 'detail'] as const,
  messages: (conversationId: string) => ['conversations', conversationId, 'messages'] as const,
  debugTraces: (conversationId: string) =>
    ['conversations', conversationId, 'debug-traces'] as const,
  debugTraceDetail: (conversationId: string, traceId: string) =>
    ['conversations', conversationId, 'debug-traces', traceId] as const,
}

/** 대화 내비게이터(사이드바/퀵스위처/대화 목록) 캐시를 무효화한다.
 *  prefix 매칭 특성상 ``list(agentId)``가 page 쿼리까지 포섭하며,
 *  ``['agents']`` 같은 광역 무효화는 무관한 쿼리 refetch를 유발하므로 금지. */
export function invalidateConversationNavigators(
  queryClient: QueryClient,
  agentId?: string | null,
  conversationId?: string | null,
): void {
  if (agentId) {
    queryClient.invalidateQueries({ queryKey: conversationKeys.list(agentId) })
  }
  if (conversationId) {
    queryClient.invalidateQueries({ queryKey: conversationKeys.detail(conversationId) })
  }
  queryClient.invalidateQueries({ queryKey: ['conversations', 'page'] })
  queryClient.invalidateQueries({ queryKey: ['agents', 'summary'] })
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
  options: ConversationPagesOptions = {},
) {
  const pageParams = normalizeConversationPageParams(params)
  return useInfiniteQuery({
    queryKey: conversationKeys.pages(agentId, pageParams),
    queryFn: ({ pageParam }) =>
      conversationsApi.page(agentId, {
        ...pageParams,
        cursor: pageParam,
    }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: (options.enabled ?? true) && !!agentId,
    refetchInterval: (query) =>
      conversationPagesContainActiveRun(query.state.data?.pages) ? 1000 : false,
  })
}

export function useGlobalConversationPages(
  params: Omit<ConversationPageParams, 'cursor'> = {},
  options: ConversationPagesOptions = {},
) {
  const pageParams = normalizeConversationPageParams(params)
  return useInfiniteQuery({
    queryKey: conversationKeys.globalPages(pageParams),
    queryFn: ({ pageParam }) =>
      conversationsApi.globalPage({
        ...pageParams,
        cursor: pageParam,
    }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: options.enabled ?? true,
    // 백그라운드 run이 보이는 동안 navigator도 1초 폴링으로 상태를 따라간다
    refetchInterval: (query) =>
      conversationPagesContainActiveRun(query.state.data?.pages) ? 1000 : false,
  })
}

export function useConversationDetail(conversationId: string, enabled = true) {
  return useQuery({
    queryKey: conversationKeys.detail(conversationId),
    queryFn: () => conversationsApi.get(conversationId),
    enabled: enabled && !!conversationId && conversationId !== 'new',
  })
}

export function useMessages(conversationId: string, enabled = true) {
  return useQuery({
    queryKey: conversationKeys.messages(conversationId),
    // envelope 전체 fetch + select로 ``Message[]``만 노출 — caller 호환을
    // 유지하면서 ``useMessagesEnvelope``과 cache를 공유한다.
    queryFn: () => conversationsApi.messagesEnvelope(conversationId),
    select: (env) => env.messages,
    enabled: enabled && !!conversationId,
    refetchOnWindowFocus: false,
  })
}

/** W7-4 — Composer 토큰 바의 cost 표시를 위해 envelope 전체에 접근하는 hook.
 *  ``useMessages``와 동일한 queryKey/queryFn을 공유해 추가 fetch 비용 없음. */
export function useMessagesEnvelope(conversationId: string, enabled = true) {
  return useQuery({
    queryKey: conversationKeys.messages(conversationId),
    queryFn: () => conversationsApi.messagesEnvelope(conversationId),
    enabled: enabled && !!conversationId,
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
    onSuccess: () => invalidateConversationNavigators(qc, agentId),
  })
}

export function useUpdateConversation(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ConversationUpdateRequest }) =>
      conversationsApi.update(id, data),
    onSuccess: (_updated, variables) =>
      invalidateConversationNavigators(qc, agentId, variables.id),
  })
}

export function useDeleteConversation(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => conversationsApi.delete(id),
    onSuccess: () => invalidateConversationNavigators(qc, agentId),
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
      invalidateConversationNavigators(qc, agentId, conversation.id)
      qc.invalidateQueries({ queryKey: ['triggers'] })
      qc.invalidateQueries({ queryKey: ['triggers', 'summary'] })
    },
  })
}
