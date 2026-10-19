import { apiFetch } from './client'
import type { AgentReadiness, ReadinessSummary } from '@/lib/types/readiness'

/** Readiness check API (plan 12.3; G-01, G-04). */
export const readinessApi = {
  get: (agentId: string) =>
    apiFetch<AgentReadiness>(`/api/agents/${encodeURIComponent(agentId)}/readiness?source=CHAT`),
  /** At most 50 ids; agents the caller may not run are left out of the answer. */
  summary: (agentIds: readonly string[]) =>
    apiFetch<Record<string, ReadinessSummary>>('/api/agents/readiness-summary', {
      method: 'POST',
      body: JSON.stringify({ agent_ids: agentIds }),
    }),
  /** S21 "소유자에게 알림": merged per credential for 24 hours on the server. */
  notifyOwner: (agentId: string) =>
    apiFetch<{ notified: number }>(
      `/api/agents/${encodeURIComponent(agentId)}/readiness/notify-owner`,
      { method: 'POST' },
    ),
}
