// Usage / spend dashboard types (M10).
//
// Mirrors the backend `GET /api/usage/daily` shape produced by
// `daily_spend_{user,agent,model}` aggregates. Re-exported via
// `lib/types/index.ts` so consumers can import from a single barrel.

export type UsageTargetKind = 'user' | 'agent' | 'model'

export type UsageGroupBy = 'date' | 'target'

export interface UsageDailyEntry {
  /** YYYY-MM-DD when group_by='date'. null when group_by='target'. */
  date: string | null
  /** UUID of the bucketed target. null for user-level rollups. */
  target_id: string | null
  /** Friendly label (agent name / model display_name / user email). null when unavailable. */
  target_label: string | null
  total_tokens_in: number
  total_tokens_out: number
  total_cost_usd: number
  request_count: number
}

export interface UsageDailyParams {
  target_kind: UsageTargetKind
  target_id?: string
  /** ISO date string YYYY-MM-DD inclusive. */
  from?: string
  /** ISO date string YYYY-MM-DD inclusive. */
  to?: string
  group_by: UsageGroupBy
}

export type UsageMetric = 'cost' | 'tokens' | 'requests'
