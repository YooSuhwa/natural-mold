/** Operator authorization-sync screen (plan 14.3, S27; H-04). Mirrors `app/schemas/admin_authz.py`. */

export type FgaStatus = 'ok' | 'degraded' | 'down' | 'disabled'
export type ReconcileRunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'skipped'

export interface FgaHealthView {
  status: FgaStatus
  latency_ms: number | null
  checked_at: string | null
  last_success_at: string | null
}

export interface ReconcileCounts {
  expected: number
  stored: number
  missing: number
  extra: number
  changed: number
  held: number
  queued: number
  failed: number
  diff: number
  repaired: number
  deferred: number
}

export interface ReconcileRunView {
  id: string
  status: ReconcileRunStatus
  trigger: 'manual' | 'scheduled'
  mode: string
  partitions_done: number
  partitions_total: number
  report: ReconcileCounts | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface AuthzOpsStatus {
  mode: string
  sync_enabled: boolean
  openfga: FgaHealthView
  outbox: { pending: number; retrying: number; failed: number; lag_seconds: number }
  revocations: { pending: number; stuck: number; oldest_seconds: number }
  reconcile: { active: ReconcileRunView | null; last: ReconcileRunView | null }
  shadow: { type: string; legacy_only: number; fga_only: number }[]
  delegations: { broken_links: number; broken_last_24h: number }
  retention: {
    enabled: boolean
    schedule: string
    last_run_at: string | null
    purges_last_24h: number
  }
  jobs: { id: string; scheduled: boolean; schedule: string | null }[]
}

export interface OutboxFailure {
  id: string
  tenant_id: string | null
  resource_type: string
  resource_id: string
  op: string
  attempts: number
  error_type: string | null
  created_at: string
  next_attempt_at: string
}

export interface OutboxFailurePage {
  items: OutboxFailure[]
}
