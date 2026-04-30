// Health check domain types — mirrors backend `app/schemas/health.py`.
// M9: surfaces periodic + on-demand health probe results for models and MCP
// servers, plus the chronological history feed used to render charts.

export type HealthStatus = 'healthy' | 'unhealthy' | 'degraded' | 'unknown'

export type HealthTargetKind = 'model' | 'mcp_server'

export interface HealthCheckEntry {
  id: string
  target_kind: HealthTargetKind
  target_id: string
  status: HealthStatus
  latency_ms: number | null
  error_kind: string | null
  error_message: string | null
  checked_at: string
  /**
   * Display name of the target (model display_name or MCP server name).
   * Present on the list-summary endpoints (`/api/health/{models,mcp-servers}`)
   * so the table can render alongside the chip without a second fetch;
   * absent on the single-probe response and the time-series history feed.
   */
  name?: string | null
}

export interface RunHealthCheckInput {
  targetKind: HealthTargetKind
  targetId: string
  /** Optional credential override — for models the chosen LLM credential. */
  credentialId?: string | null
}
