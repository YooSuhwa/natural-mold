'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  changeConversationGoal,
  getConversationGoal,
  getGoalEvidenceHistoryReference,
  putConversationGoal,
} from '@/lib/api/conversation-goals'
import type {
  GoalAction,
  GoalEnvelope,
  GoalHistoryReceipt,
  SaveGoalInput,
} from '@/lib/types/conversation-goal'

type GoalCommand = { save: SaveGoalInput } | { action: GoalAction; expected_revision: number }

export const conversationGoalKeys = {
  evidenceHistory: (receipt: GoalHistoryReceipt) =>
    ['conversation-goal', receipt.conversationId, 'history', receipt] as const,
  detail: (conversationId: string) => ['conversation-goal', conversationId] as const,
}

export function goalEvidenceDownloadPath(
  conversationId: string,
  artifactId: string,
  versionId: string,
) {
  return `/api/conversations/${encodeURIComponent(conversationId)}/goal/artifacts/${encodeURIComponent(artifactId)}/versions/${encodeURIComponent(versionId)}/download`
}

export function useConversationGoal(conversationId: string, onSuccess: () => void) {
  const client = useQueryClient()
  const detailKey = conversationGoalKeys.detail(conversationId)
  const query = useQuery({
    queryKey: detailKey,
    queryFn: () => getConversationGoal(conversationId),
    refetchInterval: ({ state }) =>
      state.data?.enabled && state.data.goal?.status === 'active' ? 10_000 : false,
  })
  const mutation = useMutation({
    mutationFn: (command: GoalCommand) =>
      'save' in command
        ? putConversationGoal(conversationId, command.save)
        : changeConversationGoal(conversationId, command),
    onSuccess: (envelope) => {
      client.setQueryData<GoalEnvelope>(detailKey, (old) =>
        (old?.goal?.revision ?? -1) > (envelope.goal?.revision ?? -1) ? old : envelope,
      )
      onSuccess()
    },
    onSettled: () => client.invalidateQueries({ queryKey: detailKey }),
  })
  return { query, mutation }
}

export function useGoalEvidenceHistoryReference(receipt: GoalHistoryReceipt) {
  return useQuery({
    queryKey: conversationGoalKeys.evidenceHistory(receipt),
    queryFn: ({ signal }) =>
      getGoalEvidenceHistoryReference(
        receipt.conversationId,
        receipt.goalId,
        receipt.goalVersion,
        receipt.versionId,
        signal,
      ),
    gcTime: 0,
    retry: false,
  })
}
