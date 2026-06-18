'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import { isActiveRunStatus } from '@/lib/chat-runs/status'
import { agentQueryKeys } from '@/lib/query-keys/agents'
import { conversationKeys } from './use-conversations'

export const conversationRunKeys = {
  active: (conversationId: string) => ['conversations', conversationId, 'runs', 'active'] as const,
  detail: (conversationId: string, runId: string) =>
    ['conversations', conversationId, 'runs', runId] as const,
}

export function useActiveConversationRun(conversationId: string, enabled = true) {
  return useQuery({
    queryKey: conversationRunKeys.active(conversationId),
    queryFn: () => conversationRunsApi.active(conversationId),
    enabled: enabled && !!conversationId,
    refetchInterval: (query) =>
      query.state.data && isActiveRunStatus(query.state.data.status) ? 1000 : false,
    refetchOnWindowFocus: false,
  })
}

export function useConversationRun(conversationId: string, runId: string | null, enabled = true) {
  return useQuery({
    queryKey: conversationRunKeys.detail(conversationId, runId ?? 'none'),
    queryFn: () => conversationRunsApi.get(conversationId, runId ?? ''),
    enabled: enabled && !!conversationId && !!runId,
    refetchOnWindowFocus: false,
  })
}

export function useCancelConversationRun(conversationId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (runId: string) => conversationRunsApi.cancel(conversationId, runId),
    onSuccess: (run, runId) => {
      queryClient.invalidateQueries({ queryKey: conversationKeys.messages(conversationId) })
      queryClient.invalidateQueries({ queryKey: conversationRunKeys.active(conversationId) })
      queryClient.invalidateQueries({ queryKey: conversationRunKeys.detail(conversationId, runId) })
      queryClient.invalidateQueries({ queryKey: agentQueryKeys.all })
      if (run.agent_id) {
        queryClient.invalidateQueries({ queryKey: conversationKeys.list(run.agent_id) })
      }
    },
  })
}
