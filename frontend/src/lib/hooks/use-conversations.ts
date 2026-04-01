"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { conversationsApi } from "@/lib/api/conversations"

export function useConversations(agentId: string) {
  return useQuery({
    queryKey: ["agents", agentId, "conversations"],
    queryFn: () => conversationsApi.list(agentId),
    enabled: !!agentId,
  })
}

export function useMessages(conversationId: string) {
  return useQuery({
    queryKey: ["conversations", conversationId, "messages"],
    queryFn: () => conversationsApi.messages(conversationId),
    enabled: !!conversationId,
  })
}

export function useCreateConversation(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (title?: string) => conversationsApi.create(agentId, title),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents", agentId, "conversations"] }),
  })
}
