'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { toolApprovalApi } from '@/lib/api/tool-approval'
import { toolApprovalQueryKeys } from '@/lib/query-keys/tool-approval'

/** The agent's tool approval settings, the company cap and the effective table. */
export function useToolApproval(agentId: string | undefined) {
  return useQuery({
    queryKey: toolApprovalQueryKeys.view(agentId ?? ''),
    queryFn: () => toolApprovalApi.view(agentId ?? ''),
    enabled: !!agentId,
    retry: false,
  })
}

/** Tools allowed for this conversation (approval-modes-plan Q5). */
export function useToolGrants(conversationId: string | undefined) {
  return useQuery({
    queryKey: toolApprovalQueryKeys.grants(conversationId ?? ''),
    queryFn: () => toolApprovalApi.grants(conversationId ?? ''),
    enabled: !!conversationId,
  })
}

export function useToolGrantMutations(conversationId: string | undefined) {
  const qc = useQueryClient()
  const refresh = () =>
    qc.invalidateQueries({ queryKey: toolApprovalQueryKeys.grants(conversationId ?? '') })
  return {
    grant: useMutation({
      mutationFn: (tool: string) => toolApprovalApi.grant(conversationId ?? '', tool),
      onSuccess: refresh,
    }),
    revoke: useMutation({
      mutationFn: (tool: string) => toolApprovalApi.revoke(conversationId ?? '', tool),
      onSuccess: refresh,
    }),
  }
}
