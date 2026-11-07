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
  | 'memory.consolidation_run'
  | 'memory.tenant_policy_update'

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
  /** Keys like `consolidation`, `needs_review`, `replaces_confirmed`, `auto_applied`. */
  flags: Record<string, unknown>
  action: MemoryProposalAction
  record_id: string | null
  /** Plan 15 (5.6): what a consolidation proposal changes, for the before/after view. */
  target_record_id: string | null
  source_ids: string[] | null
  /** Set for `action: 'profile'`. */
  profile_change: MemoryProfileChange | null
  /** The memories it changes, as they are now (the "before" side). */
  sources: MemoryProposalSource[]
  /** Why it cannot be approved in bulk; `null` = it can. */
  bulk_block: MemoryProposalBulkBlock | null
}

export type MemoryProposalAction =
  | 'create'
  | 'update'
  | 'supersede'
  | 'merge'
  | 'pin'
  | 'expire'
  | 'revoke'
  | 'profile'

export type MemoryProposalBulkBlock =
  | 'contradiction'
  | 'marked'
  | 'replaces_confirmed'
  | 'not_pending'

export interface MemoryProposalSource {
  id: string
  content: string
  status: string
  kind: string
  /** Server read-time origin label (e.g. "사람이 확인함"). */
  /** Read-time origin of the memory (plan 15, 5.4-8). */
  provenance: 'confirmed' | 'agent' | 'outside' | 'legacy'
}

/** A profile line a consolidation proposal adds or removes (plan 15, 5.12). */
export interface MemoryProfileChange {
  op: 'add' | 'remove'
  kind: MemoryProfileKind
  agent_id: string | null
  line_id?: string | null
  text: string
  /** Source memory ids. */
  sources: string[]
}

export interface MemoryBulkApproveSkip {
  id: string
  /** A `MemoryProposalBulkBlock`, `not_found`, or a lowered error code. */
  reason: string
}

export interface MemoryBulkApproveResult {
  approved: string[]
  skipped: MemoryBulkApproveSkip[]
}

/** `GET/PUT /api/me/memory-tenant-policy` values (plan 15, 5.11, Q9). */
export interface MemoryTenantPolicyValues {
  default_mode: MemoryApprovalMode
  allowed_modes: MemoryApprovalMode[]
  consolidation_enabled: boolean
  channel_personal_memory: boolean
  contacts_allowed: boolean
}

/** Company and personal tenants share the keys; only `defaults` differ (Q9). */
export interface MemoryTenantPolicy {
  tenant_kind: MemoryTenantKind
  values: MemoryTenantPolicyValues
  defaults: MemoryTenantPolicyValues
  can_manage: boolean
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
  /** `null` for a profile line change (no memory row is made or changed). */
  memory: MemoryRecord | null
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

/** Plan 15 (5.12) profile documents: 내 프로필 and one agent's 작업 메모. */
export type MemoryProfileKind = 'user_profile' | 'work_memo'
/** `user` = the person wrote it, `pinned` = 프로필에 올림, `consolidation` = 정리 작업. */
export type MemoryProfileLineOrigin = 'user' | 'pinned' | 'consolidation'

export interface MemoryProfileLine {
  id: string
  text: string
  origin: MemoryProfileLineOrigin
  /** Source memory ids. */
  sources: string[]
  /** `false`: a source memory was revoked or deleted, so runs skip the line. */
  readable: boolean
}

export interface MemoryProfile {
  kind: MemoryProfileKind
  agent_id: string | null
  lines: MemoryProfileLine[]
  tokens: number
  budget_tokens: number
  version: number
}

export interface MemoryProfiles {
  user_profile: MemoryProfile
  work_memo: MemoryProfile | null
}

/** A kept line sends its `id` with unchanged text; edited or new lines become the person's own. */
export interface MemoryProfileLineInput {
  id?: string | null
  text: string
}

export interface MemoryProfileUpdate {
  agent_id?: string | null
  lines: MemoryProfileLineInput[]
  /** The version the person edited (`MEMORY_PROFILE_CHANGED` when the server has a newer one). */
  version?: number
}

export interface MemoryRecallPreviewRequest {
  message: string
  agent_id?: string | null
  /** Earlier user messages, latest first (at most two). */
  previous?: string[]
}

export type MemoryRecallMode = 'fresh' | 'skipped' | 'reference'
export type MemoryRecallVector = 'on' | 'unmeasured' | 'off'

export interface MemoryRecallPreviewItem {
  id: string
  scope: string
  kind: string
  content: string
  keyword: number
  cosine: number | null
  search: number
  final: number
  included: boolean
  left_out: 'budget' | 'scope_cap' | null
}

/** `POST /api/me/memory-recall-preview`: what a chat run would recall now (nothing recorded). */
export interface MemoryRecallPreview {
  mode: MemoryRecallMode
  /** `nori`, `suffix`, or `''` when nothing was searched. */
  analyzer: string
  vector: MemoryRecallVector
  profile_lines: string[]
  items: MemoryRecallPreviewItem[]
  tokens: number
  budget_tokens: number
}

// Plan 15 Q14: the company's memory vector model (`/api/me/memory-vector`).

export interface MemoryVectorModel {
  id: string
  display_name: string
  model_name: string
  provider: string
  dimensions: number
  source: 'platform' | 'company'
}

export interface MemoryVectorStatus {
  /** `null` = vectors are off (keyword recall only). */
  model: MemoryVectorModel | null
  /** Registered embedding models the company may choose. */
  candidates: MemoryVectorModel[]
  /** Memories recall may embed. */
  total: number
  /** Of those, how many have a vector of the chosen model. */
  embedded: number
  /** Vectors of any other model (removed once a re-embedding finishes). */
  other_vectors: number
  /** Latest re-embedding job: queued, running, succeeded, failed or canceled. */
  job_state: string | null
  job_progress: number | null
  /** False: no measured cosine floor for the chosen model, so recall stays keyword-only. */
  floor_measured: boolean
  /** Whether this person can change it (a company admin, or a personal tenant's owner). */
  can_manage: boolean
}

export interface MemoryVectorUpdate {
  model_id: string | null
  /** The number of memories the screen said would be embedded again (0 to turn off). */
  confirm_count: number
  /** Turning off: also remove the stored vectors. */
  drop_vectors?: boolean
}
