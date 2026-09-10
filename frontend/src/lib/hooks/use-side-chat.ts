'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { conversationsApi } from '@/lib/api/conversations'
import { invalidateConversationNavigators } from './use-conversations'

export function useCreateSideChat() {
  return useMutation({ mutationFn: conversationsApi.createSideChat })
}

export function useSaveSideChat(agentId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: conversationsApi.saveSideChat,
    onSuccess: (conversation) => {
      invalidateConversationNavigators(queryClient, agentId, conversation.id)
    },
  })
}
