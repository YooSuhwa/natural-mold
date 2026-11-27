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
