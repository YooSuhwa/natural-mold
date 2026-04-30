// Health check client (M9). Wraps the backend probe endpoints that surface
// the latest snapshot per target plus a paginated time-series history.

import { apiFetch } from './client'
import type { HealthCheckEntry, HealthTargetKind, RunHealthCheckInput } from '@/lib/types/health'

const DEFAULT_HISTORY_LIMIT = 30

export const healthApi = {
  /** Latest snapshot per registered model (one row per model_id). */
  listModels: () => apiFetch<HealthCheckEntry[]>('/api/health/models'),

  /** Latest snapshot per registered MCP server. */
  listMcpServers: () => apiFetch<HealthCheckEntry[]>('/api/health/mcp-servers'),

  /**
   * Chronological probe history for a single target (oldest → newest). Used by
   * the latency line chart and the status timeline strip.
   */
  history: (
    targetKind: HealthTargetKind,
    targetId: string,
    limit: number = DEFAULT_HISTORY_LIMIT,
  ) => {
    const qs = new URLSearchParams({
      target_kind: targetKind,
      target_id: targetId,
      limit: String(limit),
    })
    return apiFetch<HealthCheckEntry[]>(`/api/health/history?${qs}`)
  },

  /**
   * Run a probe immediately and return the new entry. Caller is responsible
   * for invalidating list/history caches via the React Query mutation hook.
   */
  runCheck: ({ targetKind, targetId, credentialId }: RunHealthCheckInput) => {
    const qs = new URLSearchParams({
      target_kind: targetKind,
      target_id: targetId,
    })
    if (credentialId) qs.set('credential_id', credentialId)
    return apiFetch<HealthCheckEntry>(`/api/health/check?${qs}`, {
      method: 'POST',
    })
  },
}
