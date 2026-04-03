import { apiFetch } from './client'
import type { UsageSummary } from '@/lib/types'

export const usageApi = {
  agentUsage: (agentId: string, period?: string) =>
    apiFetch<Record<string, unknown>>(
      `/api/agents/${agentId}/usage${period ? `?period=${period}` : ''}`,
    ),
  summary: (period?: string) =>
    apiFetch<UsageSummary>(`/api/usage/summary${period ? `?period=${period}` : ''}`),
}
