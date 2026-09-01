'use client'

import { useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { conversationsApi } from '@/lib/api/conversations'
import { conversationKeys } from '@/lib/hooks/use-conversations'
import { dispatchMoldyBranchSwitched } from '@/lib/chat/langgraph-runtime/branch-switch-events'

export function useConversationBranchSwitch(
  conversationId: string | null,
): (checkpointId: string) => Promise<void> {
  const queryClient = useQueryClient()
  return useCallback(
    async (checkpointId: string) => {
      if (!conversationId) return
      await conversationsApi.switchBranch(conversationId, checkpointId)
      await queryClient.refetchQueries({
        queryKey: conversationKeys.messages(conversationId),
        type: 'active',
      })
      dispatchMoldyBranchSwitched({ conversationId, checkpointId })
    },
    [conversationId, queryClient],
  )
}
