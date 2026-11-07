export type MemoryScope = 'user' | 'agent'
export type MemoryScopeFilter = 'all' | MemoryScope
export type MemoryAllowedScopes = 'user' | 'agent' | 'both'
export type MemoryWritePolicy = 'off' | 'ask' | 'auto'
export type TriggerMemoryWritePolicy = 'off' | 'auto'
export type AgentMemoryPolicyOverride = 'inherit' | 'off' | 'ask' | 'auto'
export type AgentMemoryScopesOverride = 'inherit' | 'agent_only' | 'user_and_agent'
export type AgentTriggerMemoryPolicyOverride = 'inherit' | 'off' | 'auto'
export type MemoryRecordStatus = 'active' | 'superseded' | 'revoked' | 'deleted'
/** `GET /api/memories?status=`; `review` = saved and marked "나중에 검토" (plan 15, 5.11). */
export type MemoryStatusFilter = 'active' | 'revoked' | 'review' | 'all'
export type MemoryKind =
  | 'fact'
  | 'preference'
  | 'commitment'
  | 'experience'
  | 'episode'
  | 'procedure'
export type MemoryOrigin =
  | 'user_stated'
  | 'agent_inferred'
  | 'legacy'
  | 'consolidation'
  | 'tool_derived'
export type MemorySensitivity = 'normal' | 'contact' | 'sensitive'
export type MemoryApprovalChannel = 'direct' | 'proposal' | 'auto_policy'
/** Plan 15 (5.11) approval presets; `custom` takes `approval_axes`. */
export type MemoryApprovalMode =
  | 'off'
  | 'read_only'
  | 'approve_all'
  | 'partial_auto'
  | 'auto_notify'
  | 'custom'
export type MemoryApprovalAxis = 'auto' | 'approve' | 'off'
export type MemoryTenantKind = 'company' | 'personal'
export type MemoryProposalStatus = 'pending' | 'approved' | 'rejected' | 'expired'
export type MemoryEventType =
  | 'memory_proposed'
  | 'memory_saved'
  | 'memory_rejected'
  | 'memory_deleted'

export interface UserMemorySettings {
  memory_enabled: boolean
  memory_read_enabled: boolean
  memory_write_policy: MemoryWritePolicy
  allowed_scopes: MemoryAllowedScopes
  trigger_memory_write_policy: TriggerMemoryWritePolicy
  /** `null` until the person picks a mode (the tenant default applies). */
  approval_mode: MemoryApprovalMode | null
  approval_axes: MemoryApprovalAxes | null
  consolidation_enabled: boolean
}

export interface MemoryApprovalAxes {
  records: MemoryApprovalAxis
  profile: MemoryApprovalAxis
  consolidation: MemoryApprovalAxis
}

/** PATCH body: explicit `null` is rejected for every field but `approval_axes`. */
export interface UserMemorySettingsUpdate {
  memory_enabled?: boolean
  memory_read_enabled?: boolean
  memory_write_policy?: MemoryWritePolicy
  allowed_scopes?: MemoryAllowedScopes
  trigger_memory_write_policy?: TriggerMemoryWritePolicy
  approval_mode?: MemoryApprovalMode
  approval_axes?: MemoryApprovalAxes | null
  consolidation_enabled?: boolean
}

/** `GET /api/me/memory-policy`: the mode in force and what the tenant allows (Q9). */
export interface MemoryPolicy {
  tenant_kind: MemoryTenantKind
  effective_mode: MemoryApprovalMode
  axes: MemoryApprovalAxes
  allowed_modes: MemoryApprovalMode[]
  default_mode: MemoryApprovalMode
  consolidation_enabled: boolean
  tenant_consolidation_enabled: boolean
}

export interface AgentMemorySettings {
  memory_policy_override: AgentMemoryPolicyOverride
  memory_scopes_override: AgentMemoryScopesOverride
  trigger_memory_policy_override: AgentTriggerMemoryPolicyOverride
}

export type AgentMemorySettingsUpdate = Partial<AgentMemorySettings>

export interface MemoryRecord {
  id: string
  user_id: string
  agent_id: string | null
  scope: MemoryScope
  content: string
  reason: string | null
  store_path: string
  source_conversation_id: string | null
  source_message_id: string | null
  source_run_id: string | null
  status: MemoryRecordStatus
  created_at: string
  updated_at: string
  deleted_at: string | null
  kind: MemoryKind
  subject: 'self' | 'other'
  subject_label: string | null
  topic: string | null
  title: string | null
  pinned: boolean
  origin: MemoryOrigin
  sensitivity: MemorySensitivity
  importance: number
  occurred_start: string | null
  occurred_end: string | null
  valid_until: string | null
  approved_by: string | null
  approved_at: string | null
  approval_channel: MemoryApprovalChannel | null
  revoked_at: string | null
  revoke_reason: string | null
  recall_count: number
  /** Keys like `instruction_like`, `sensitive`, `other_person`, `contact`, `review_later`. */
  flags: Record<string, unknown>
}

export interface MemoryRevision {
  id: string
  action: string
  actor_user_id: string | null
  run_path: string | null
  before_content: string | null
  after_content: string | null
  reason: string | null
  created_at: string
}

export type MemoryScopeSelection = 'all' | MemoryScope

export interface MemoryScopeQuery {
  scope: MemoryScopeSelection
  agentId?: string | null
}

export interface MemoryScopeCount {
  count: number
}

export interface MemoryScopeDelete {
  scope: MemoryScopeSelection
  agent_id?: string | null
  /** Must equal the count the person confirmed (`MEMORY_BULK_COUNT_CHANGED` otherwise). */
  confirm_count: number
}

/** What a delete erased and what it cannot reach (plan 15, 5.7 삭제 영수증). */
export interface MemoryDeletionReceipt {
  deleted: number
  revisions_removed: number
  proposals_cleared: number
  derived_marked: number
  kept_elsewhere: string[]
}

export type MemoryExportFormat = 'json' | 'markdown'

export type MemoryActivityAction =
  | 'memory.record_create'
  | 'memory.record_update'
  | 'memory.record_delete'
  | 'memory.record_delete_bulk'
  | 'memory.record_revoke'
  | 'memory.record_restore'
  | 'memory.proposal_create'
  | 'memory.proposal_approve'
  | 'memory.proposal_reject'
  | 'memory.export'

/** One of the person's own memory audit events; never carries memory text. */
export interface MemoryActivity {
  id: string
  action: string
  target_type: string
  target_id: string | null
  created_at: string
  metadata: Record<string, unknown>
}

export interface MemoryRecordCreate {
  scope: MemoryScope
  content: string
  reason?: string | null
  agent_id?: string | null
  source_conversation_id?: string | null
  source_message_id?: string | null
  source_run_id?: string | null
}

export interface MemoryRecordUpdate {
  content?: string
  reason?: string | null
}

export interface MemoryProposal {
  id: string
  user_id: string
  agent_id: string | null
  conversation_id: string | null
  source_run_id: string | null
  scope: MemoryScope
  content: string
  reason: string | null
  status: MemoryProposalStatus
  created_at: string
  resolved_at: string | null
  kind: MemoryKind
  subject: 'self' | 'other'
  topic: string | null
  origin: MemoryOrigin
  sensitivity: MemorySensitivity
  flags: Record<string, unknown>
  action: string
  record_id: string | null
}

export type MemoryProposalStatusFilter = 'pending' | 'approved' | 'rejected' | 'all'

export interface MemoryProposalCreate {
  scope: MemoryScope
  content: string
  reason?: string | null
  agent_id?: string | null
  conversation_id?: string | null
  source_run_id?: string | null
}

export interface MemoryProposalEditApprove {
  content: string
  reason?: string | null
}

export interface MemoryProposalApproval {
  proposal: MemoryProposal
  memory: MemoryRecord
}

export interface MemoryEventPayload {
  id?: string
  scope: MemoryScope
  content: string
  reason?: string | null
  policy?: MemoryWritePolicy | TriggerMemoryWritePolicy
  agent_id?: string | null
  conversation_id?: string | null
  source_run_id?: string | null
}
