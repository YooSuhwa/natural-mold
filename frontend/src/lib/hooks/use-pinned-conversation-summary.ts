'use client'

import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { pinnedConversationSummaryApi } from '@/lib/api/pinned-conversation-summary'

export const pinnedConversationSummaryKeys = {
  detail: (conversationId: string) => ['conversations', conversationId, 'pinned-summary'] as const,
  mutation: (conversationId: string) =>
    ['conversations', conversationId, 'pinned-summary-mutation'] as const,
}

export function usePinnedConversationSummary(conversationId: string | null) {
  const queryClient = useQueryClient()
  const conversationKey = conversationId ?? 'none'
  const queryKey = pinnedConversationSummaryKeys.detail(conversationKey)
  const mutationKey = pinnedConversationSummaryKeys.mutation(conversationKey)
  const mutationScope = { id: mutationKey.join(':') }
  const activeMutationCount = useIsMutating({ mutationKey, exact: true })
  const query = useQuery({
    queryKey,
    queryFn: () => pinnedConversationSummaryApi.get(conversationId ?? ''),
    enabled: conversationId !== null,
  })
  const pin = useMutation({
    mutationKey,
    scope: mutationScope,
    mutationFn: (messageId: string) =>
      pinnedConversationSummaryApi.pin(conversationId ?? '', messageId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey, exact: true }),
  })
  const unpin = useMutation({
    mutationKey,
    scope: mutationScope,
    mutationFn: () => pinnedConversationSummaryApi.unpin(conversationId ?? ''),
    onSuccess: () => queryClient.invalidateQueries({ queryKey, exact: true }),
  })
  return {
    summary: query.data?.summary ?? null,
    isLoading: query.isLoading,
    isMutating: activeMutationCount > 0,
    pin: pin.mutate,
    unpin: unpin.mutate,
  }
}
