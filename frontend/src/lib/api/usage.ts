import { apiFetch } from './client'
import type { UsageDailyEntry, UsageDailyParams, UsageSummary } from '@/lib/types'

function buildDailyQuery(params: UsageDailyParams): string {
  const search = new URLSearchParams()
  search.set('target_kind', params.target_kind)
  search.set('group_by', params.group_by)
  if (params.target_id) search.set('target_id', params.target_id)
  if (params.from) search.set('from', params.from)
  if (params.to) search.set('to', params.to)
  return search.toString()
}

export const usageApi = {
  agentUsage: (agentId: string, period?: string) =>
    apiFetch<Record<string, unknown>>(
      `/api/agents/${agentId}/usage${period ? `?period=${period}` : ''}`,
    ),
  summary: (period?: string) =>
    apiFetch<UsageSummary>(`/api/usage/summary${period ? `?period=${period}` : ''}`),
  /**
   * GET /api/usage/daily — daily aggregated spend (M10).
   *
   * `group_by='date'` returns rows keyed by day (date populated, target_id null).
   * `group_by='target'` returns rows keyed by target id (date null, target_id populated).
   */
  daily: (params: UsageDailyParams) =>
    apiFetch<UsageDailyEntry[]>(`/api/usage/daily?${buildDailyQuery(params)}`),
}

export async function getDailyAggregate(params: UsageDailyParams): Promise<UsageDailyEntry[]> {
  return usageApi.daily(params)
}
