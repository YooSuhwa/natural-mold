/** Approval inbox, run status and emergency stop (feature plan v2 순번 13, chapter 17). */

export type ApprovalStatus = 'pending' | 'approved' | 'edited' | 'rejected' | 'expired' | 'canceled'
export type ApprovalSource = 'chat' | 'trigger' | 'api' | 'subagent'
export type ApprovalDecisionType = 'approve' | 'edit' | 'reject'

export interface ApprovalAction {
  name: string
  args: unknown
  description: string | null
  allowed_decisions: string[]
}

export interface ApprovalView {
  id: string
  status: ApprovalStatus
  source: ApprovalSource
  agent_id: string | null
  agent_name: string
  conversation_id: string
  trigger_id: string | null
  actions: ApprovalAction[]
  attempt: number
  policy_version: number | null
  channel: string | null
  reason: string | null
  created_at: string
  expires_at: string
  decided_at: string | null
}

export interface ApprovalPage {
  items: ApprovalView[]
}

export interface InboxDecision {
  type: ApprovalDecisionType
  edited_action?: { args: Record<string, unknown> }
  message?: string
}

export type RunState = 'running' | 'waiting' | 'failed'

export interface RunStatusView {
  run_id: string
  state: RunState
  status: string
  agent_id: string | null
  agent_name: string
  source: string | null
  mine: boolean
  conversation_id: string | null
  error_code: string | null
  created_at: string
  completed_at: string | null
}

export type StopScope = 'agent' | 'user' | 'mcp_server'

export interface EmergencyStopState {
  block_received: boolean
  new_calls_blocked: boolean
  runs_cancel_requested: number
  runs_cancel_confirmed: number
  schedules_paused: number
  deployments_disabled: number
  approvals_canceled: number
  external_side_effects: 'unknown'
}

export interface EmergencyStopView {
  id: string
  scope_type: StopScope
  scope_id: string
  scope_name: string
  status: 'active' | 'released'
  reason: string
  stopped_by: string | null
  stopped_at: string
  released_by: string | null
  released_at: string | null
  release_reason: string | null
  state: EmergencyStopState
}
