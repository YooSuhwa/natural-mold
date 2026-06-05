export type MemoryScope = 'user' | 'agent'
export type MemoryScopeFilter = 'all' | MemoryScope
export type MemoryAllowedScopes = 'user' | 'agent' | 'both'
export type MemoryWritePolicy = 'off' | 'ask' | 'auto'
export type TriggerMemoryWritePolicy = 'off' | 'auto'
export type AgentMemoryPolicyOverride = 'inherit' | 'off' | 'ask' | 'auto'
export type AgentMemoryScopesOverride = 'inherit' | 'agent_only' | 'user_and_agent'
export type AgentTriggerMemoryPolicyOverride = 'inherit' | 'off' | 'auto'
export type MemoryRecordStatus = 'active' | 'deleted'
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
}

export type UserMemorySettingsUpdate = Partial<UserMemorySettings>

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
}

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
