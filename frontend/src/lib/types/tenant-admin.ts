/**
 * Company settings types (plan 13.14 S05–S07; U-02). Mirror backend
 * `app/schemas/tenant_overview.py`, `tenant_publication.py`, `tenant_members.py`,
 * `tenant_invitations.py` and `tenant_catalog.py`.
 */

import type { TenantOverview } from './organization'

export type TenantAccess = TenantOverview['access']

/** Admin/auditor overview variants add counts (`TenantAuditorOverview`/`TenantAdminOverview`). */
export interface TenantOverviewDetail extends TenantOverview {
  active_member_count?: number
  organization_count?: number
  active_admin_count?: number
}

// ----------------------------- S05 policies and approvals -----------------------------

export type TenantPublishPolicy = 'disabled' | 'open' | 'approval'

export interface RetentionPreview {
  conversations: number
  agents: number
  removed_member_conversations: number
  removed_member_memories: number
  audit_events: number
}

export interface TenantPolicies {
  marketplace_tenant_publish: TenantPublishPolicy
  audit_retention_days: number
  updated_at: string
  /** PUT only: a shortened retention was not saved yet; resend with `confirm`. */
  preview?: RetentionPreview | null
}

export interface TenantPoliciesUpdate {
  marketplace_tenant_publish: TenantPublishPolicy
  audit_retention_days?: number | null
  confirm?: boolean
}

export interface PublicationRequestItem {
  item_id: string
  name: string
  description: string | null
  resource_type: string
  organization: { id: string; name: string }
  publisher: { id: string | null; name: string | null }
  requested_at: string | null
  credential_types: string[]
}

export interface TenantPage<Item> {
  items: Item[]
  next_cursor: string | null
  has_more: boolean
}

// ----------------------------- S06 members and invitations -----------------------------

export type TenantRole = 'admin' | 'auditor'
export type TenantMemberStatus = 'active' | 'suspended' | 'removed'

export interface TenantMember {
  user_id: string
  display_name: string
  email: string
  status: TenantMemberStatus
  roles: TenantRole[]
  created_at: string
}

export type TenantInvitationStatus = 'pending' | 'accepted' | 'revoked' | 'expired'

export interface TenantInvitation {
  id: string
  email: string
  role: TenantRole | null
  expires_at: string
  created_at: string
  status: TenantInvitationStatus
}

/** The one-time `token` is only ever handed to the clipboard, never rendered. */
export interface TenantInvitationCreated extends TenantInvitation {
  token: string | null
}

export interface TenantInvitationCreate {
  email: string
  roles: TenantRole[]
}

// ----------------------------- S07 models, credentials, LLM, catalog -----------------------------

export type CatalogSource = 'platform' | 'company'

export interface TenantModel {
  id: string
  source: CatalogSource
  provider: string
  model_name: string
  display_name: string
  base_url: string | null
  is_default: boolean
  is_visible: boolean
  default_credential_id: string | null
  company_enabled: boolean
  requires_company_key: boolean
  /** `null` = every organization of the company (platform models only). */
  allowed_org_ids: string[] | null
}

export interface TenantModelList {
  items: TenantModel[]
  allow_system_models: boolean
}

export interface TenantModelCreate {
  provider: string
  model_name: string
  display_name: string
  base_url?: string | null
  default_credential_id?: string | null
  allowed_org_ids: string[]
}

/** Platform models accept only `allowed_org_ids`. */
export interface TenantModelUpdate {
  display_name?: string
  base_url?: string | null
  default_credential_id?: string | null
  allowed_org_ids?: string[] | null
}

export interface TenantCredential {
  id: string
  definition_key: string
  name: string
  field_keys: string[]
  status: string
  allowed_org_ids: string[]
  created_at: string
  updated_at: string
}

export interface TenantCredentialCreate {
  definition_key: string
  name: string
  data: Record<string, unknown>
  allowed_org_ids: string[]
}

export interface TenantCredentialUpdate {
  name?: string
  data?: Record<string, unknown>
  status?: 'active' | 'disabled'
  allowed_org_ids?: string[]
}

export type CatalogKind = 'model' | 'tool' | 'skill' | 'mcp_server'

export interface TenantCatalogItem {
  type: CatalogKind
  id: string
  name: string
  source: CatalogSource
  platform_available: boolean
  company_enabled: boolean
}

export interface TenantCatalogPolicy {
  items: TenantCatalogItem[]
  allow_system_models: boolean
  updated_at: string
}

export interface CatalogOverride {
  type: CatalogKind
  id: string
  enabled: boolean
}

export type SystemLlmRole = 'text_primary' | 'text_fallback' | 'image'

export interface TenantLlmSetting {
  role: SystemLlmRole
  inherits: boolean
  credential_id: string | null
  credential_name: string | null
  provider: string | null
  model_name: string | null
  configured: boolean
}

export interface TenantLlmSettings {
  items: TenantLlmSetting[]
  allow_system_models: boolean
}

/** Both `null` drops the company override (inherit the platform default). */
export interface TenantLlmSettingUpdate {
  role: SystemLlmRole
  credential_id: string | null
  model_name: string | null
}
