/** Approval inbox, run status and emergency stop (feature plan v2 순번 13, chapter 17). */

export type ApprovalStatus = 'pending' | 'approved' | 'edited' | 'rejected' | 'expired' | 'canceled'
export type ApprovalSource = 'chat' | 'trigger' | 'api' | 'subagent' | 'workflow' | 'registration'
/**
 * ``business``: a workflow approval node, which has no conversation. ``registration``: a
 * member's tool or MCP server waiting for an administrator (no agent, no run).
 */
export type ApprovalKind = 'tool' | 'business' | 'registration'
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
  kind: ApprovalKind
  conversation_id: string | null
  workflow_run_id: string | null
  trigger_id: string | null
  /** ``registration`` only: what waits to be registered. */
  resource_type?: RegistrationResourceType | null
  resource_id?: string | null
  actions: ApprovalAction[]
  attempt: number
  policy_version: number | null
  channel: string | null
  reason: string | null
  created_at: string
  expires_at: string
  decided_at: string | null
}

export type RegistrationResourceType = 'mcp_server' | 'tool'
export type RegistrationState = 'approved' | 'pending' | 'rejected'

/** The last request for a tool or MCP server's registration, as its registrant sees it. */
export interface RegistrationRequestView {
  id: string
  status: ApprovalStatus
  /** The administrator's note on a decision; a short code when the request was retired. */
  reason: string | null
  requested_at: string
  decided_at: string | null
}

export interface RegistrationStatus {
  state: RegistrationState
  can_request: boolean
  /** An MCP server whose tool versions administrators review instead of its managers. */
  administrators_review_tools: boolean
  request: RegistrationRequestView | null
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
