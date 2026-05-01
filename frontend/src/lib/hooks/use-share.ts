'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { sharesApi } from '@/lib/api/shares'

export const shareKeys = {
  active: (conversationId: string) => ['conversations', conversationId, 'share'] as const,
  public: (shareToken: string) => ['shares', shareToken] as const,
}

/** Owner-side: fetch the active share link for a conversation. */
export function useActiveShare(conversationId: string, enabled = true) {
  return useQuery({
    queryKey: shareKeys.active(conversationId),
    queryFn: () => sharesApi.getActive(conversationId),
    enabled: !!conversationId && enabled,
  })
}

/** Owner-side: publish the conversation (idempotent). */
export function useCreateShare(conversationId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => sharesApi.create(conversationId),
    onSuccess: (link) =>
      qc.setQueryData(shareKeys.active(conversationId), link),
  })
}

/** Owner-side: revoke the active share (idempotent). */
export function useRevokeShare(conversationId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => sharesApi.revoke(conversationId),
    onSuccess: () => qc.setQueryData(shareKeys.active(conversationId), null),
  })
}

/** Public visitor: fetch the read-only conversation snapshot. */
export function usePublicShare(shareToken: string) {
  return useQuery({
    queryKey: shareKeys.public(shareToken),
    queryFn: () => sharesApi.getPublic(shareToken),
    enabled: !!shareToken,
    // Public pages render once per visit; refetch on window focus would
    // surprise visitors with content shifts and bumps load on the public
    // endpoint for no benefit.
    refetchOnWindowFocus: false,
  })
}
