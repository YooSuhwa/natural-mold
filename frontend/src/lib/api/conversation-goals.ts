import { apiFetch } from './client'

import type { GoalAction, GoalEnvelope, SaveGoalInput } from '@/lib/types/conversation-goal'

const goalPath = (id: string) => `/api/conversations/${encodeURIComponent(id)}/goal`

export function getConversationGoal(id: string) {
  return apiFetch<GoalEnvelope>(goalPath(id))
}

export function putConversationGoal(id: string, input: SaveGoalInput) {
  return apiFetch<GoalEnvelope>(goalPath(id), { method: 'PUT', body: JSON.stringify(input) })
}

export function changeConversationGoal(
  id: string,
  input: { expected_revision: number; action: GoalAction },
) {
  return apiFetch<GoalEnvelope>(`${goalPath(id)}/actions`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export interface GoalEvidenceHistoryReference {
  checkpoint_id: string
  history_id: string
  cutoff_index: number
  source_run_id: string
}

export function getGoalEvidenceHistoryReference(
  conversationId: string,
  goalId: string,
  goalVersion: number,
  versionId: string,
  signal: AbortSignal,
) {
  const search = new URLSearchParams({ goal_id: goalId, goal_version: String(goalVersion) })
  return apiFetch<GoalEvidenceHistoryReference | null>(
    `${goalPath(conversationId)}/evidence/${encodeURIComponent(versionId)}/history-reference?${search}`,
    { signal, cache: 'no-store' },
  )
}
