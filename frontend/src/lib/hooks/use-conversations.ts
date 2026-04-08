'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { conversationsApi } from '@/lib/api/conversations'
import type { ConversationUpdateRequest } from '@/lib/types'

export const conversationKeys = {
  list: (agentId: string) => ['agents', agentId, 'conversations'] as const,
  messages: (conversationId: string) => ['conversations', conversationId, 'messages'] as const,
}

export function useConversations(agentId: string) {
  return useQuery({
    queryKey: conversationKeys.list(agentId),
    queryFn: () => conversationsApi.list(agentId),
    enabled: !!agentId,
  })
}

export function useMessages(conversationId: string) {
  return useQuery({
    queryKey: conversationKeys.messages(conversationId),
    queryFn: () => conversationsApi.messages(conversationId),
    enabled: !!conversationId,
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
