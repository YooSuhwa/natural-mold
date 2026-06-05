export type AuditScope = 'mine' | 'all'

export type AuditOutcome = 'success' | 'failure' | 'denied' | 'skipped' | string

export interface AuditEvent {
  id: string
  actor_type: string
  actor_user_id: string | null
  actor_api_key_id: string | null
  actor_email_snapshot: string | null
  actor_label: string | null
  owner_user_id: string | null
  owner_email_snapshot: string | null
  action: string
  target_type: string
  target_id: string | null
  target_name_snapshot: string | null
  target_owner_user_id: string | null
  outcome: AuditOutcome
  reason_code: string | null
  reason_message: string | null
  request_id: string | null
  trace_id: string | null
  run_id: string | null
  ip_address: string | null
  user_agent: string | null
  metadata: Record<string, unknown> | null
  created_at: string
}

export interface AuditEventPage {
  items: AuditEvent[]
  next_cursor: string | null
}

export interface AuditEventListParams {
  scope?: AuditScope
  limit?: number
  cursor?: string | null
  action?: string | null
  target_type?: string | null
  outcome?: string | null
  actor_user_id?: string | null
  owner_user_id?: string | null
  request_id?: string | null
  trace_id?: string | null
  run_id?: string | null
  created_from?: string | null
  created_to?: string | null
}
