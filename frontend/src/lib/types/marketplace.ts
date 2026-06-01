// Marketplace domain types — mirror backend `app/marketplace/schemas.py`.
// Phase 1 (ADR-017) — Skill marketplace only. Agents/MCP entries reserved
// for Phase 2+ but already typed so the same components can branch by
// ``resource_type``.

export type MarketplaceResourceType = 'agent' | 'mcp' | 'skill'

export type MarketplaceVisibility = 'private' | 'restricted' | 'public' | 'unlisted' | 'system'

export type MarketplaceStatus = 'draft' | 'published' | 'deprecated' | 'disabled'

export type OriginKind =
  | 'created_by_me'
  | 'imported_by_me'
  | 'built_in_k_skill'
  | 'shared_with_me'
  | 'community'
  | 'system_seed'

export type PublicationState =
  | 'not_published'
  | 'draft'
  | 'published_private'
  | 'published_restricted'
  | 'published_public_listed'
  | 'published_public_unlisted'
  | 'published_unlisted'
  | 'disabled'

export type InstallationStatus = 'active' | 'needs_setup' | 'disabled' | 'uninstalled'

export type CredentialSummaryStatus =
  | 'none'
  | 'optional'
  | 'required'
  | 'hosted_proxy'
  | 'manual_login'

export type SupportLevel =
  | 'ready_python'
  | 'proxy_http'
  | 'node_package'
  | 'browser_or_local'
  | 'manual_only'
  | 'disabled'

export interface ResourceOriginSummary {
  kind: OriginKind
  label: string
  source_name?: string | null
  source_user_id?: string | null
  marketplace_item_id?: string | null
  marketplace_version_id?: string | null
}

export interface ResourcePublicationSummary {
  state: PublicationState
  item_id?: string | null
  visibility?: MarketplaceVisibility | null
  status?: MarketplaceStatus | null
  is_listed: boolean
  latest_version_id?: string | null
  version_number?: number | null
  shared_user_count: number
}

export interface InstallationSummary {
  installed: boolean
  installation_id?: string | null
  installed_resource_id?: string | null
  status?: InstallationStatus | null
  update_available: boolean
  dirty: boolean
}

export interface CredentialSummary {
  status: CredentialSummaryStatus
  required_count: number
  optional_count: number
  missing_required_count: number
}

export interface ExecutionProfile {
  support_level?: SupportLevel
  runners?: string[]
  requires_network?: boolean
  timeout_seconds?: number
  tool_dependencies?: string[]
  notes?: string | null
  [key: string]: unknown
}

export interface MarketplaceVersionSummary {
  id: string
  version_label: string
  version_number: number
  content_hash: string
  source_commit?: string | null
  created_at: string
}

export interface MarketplaceVersionDetail extends MarketplaceVersionSummary {
  item_id: string
  resource_type: MarketplaceResourceType
  payload_kind: 'skill_package' | 'agent_spec' | 'mcp_template'
  payload: Record<string, unknown>
  size_bytes: number
  credential_requirements?: Array<Record<string, unknown>> | null
  dependency_requirements?: Array<Record<string, unknown>> | null
  execution_profile?: ExecutionProfile | null
  release_notes?: string | null
  source_ref?: string | null
  source_path?: string | null
}

export interface MarketplaceItem {
  id: string
  resource_type: MarketplaceResourceType
  name: string
  slug: string
  description: string | null
  icon_id?: string | null
  icon_url?: string | null
  visibility: MarketplaceVisibility
  status: MarketplaceStatus
  is_system: boolean
  is_listed: boolean
  tags?: string[] | null
  categories?: string[] | null
  locale?: string | null
  created_at: string
  updated_at: string
  published_at?: string | null
  latest_version?: MarketplaceVersionSummary | null
  credential_summary: CredentialSummary
  execution_profile?: ExecutionProfile | null
  origin_summary?: ResourceOriginSummary | null
  publication_summary: ResourcePublicationSummary
  /** owner / super_user 시점에만 채워진다 — ACL revoke UI 용. */
  acl_user_ids?: string[] | null
  installation: InstallationSummary
}

// ---------------------------------------------------------------------------
// Credential requirements (skill side + publish side)
// ---------------------------------------------------------------------------

export type RequirementInjection = 'env' | 'config'
export type RequirementScope = 'user' | 'system_dependency' | 'manual'

export interface CredentialRequirement {
  key: string
  definition_key: string
  required: boolean
  label: string
  description?: string | null
  fields: string[]
  injection: RequirementInjection
  scope: RequirementScope
}

export interface CredentialRequirementInput extends CredentialRequirement {
  env_map?: Record<string, string> | null
}

export interface SkillCredentialBinding {
  id: string
  requirement_key: string
  credential_id: string
  scope: 'skill' | 'agent_skill'
  created_at: string
  updated_at: string
}

// ---------------------------------------------------------------------------
// Install / update
// ---------------------------------------------------------------------------

export type InstallMode = 'reuse_or_update' | 'new_copy' | 'overwrite_existing'
export type InstallMissingCredentialsMode = 'reject' | 'needs_setup'
export type UpdateStrategy = 'overwrite' | 'install_new_copy' | 'keep_current'

export interface InstallMarketplaceItemBody {
  version_id?: string | null
  name_override?: string | null
  credential_bindings?: Record<string, string>
  install_missing_credentials?: InstallMissingCredentialsMode
  install_mode?: InstallMode
}

export interface UpdateInstallationBody {
  strategy: UpdateStrategy
}

export interface MarketplaceInstallation {
  id: string
  item_id: string
  version_id: string
  resource_type: MarketplaceResourceType
  installed_skill_id?: string | null
  installed_agent_id?: string | null
  installed_mcp_server_id?: string | null
  install_status: InstallationStatus
  is_dirty: boolean
  installed_at: string
  updated_at: string
}

// ---------------------------------------------------------------------------
// Publish / manage
// ---------------------------------------------------------------------------

export interface PublishSkillBody {
  item_id?: string | null
  visibility: 'private' | 'restricted' | 'public' | 'unlisted'
  name: string
  description?: string | null
  icon_id?: string | null
  tags?: string[]
  categories?: string[]
  release_notes?: string | null
  credential_requirements?: CredentialRequirementInput[]
  acl_user_ids?: string[]
}

export interface PublishNewVersionBody {
  release_notes?: string | null
}

export interface MarketplaceItemPatchBody {
  name?: string | null
  description?: string | null
  icon_id?: string | null
  icon_url?: string | null
  tags?: string[] | null
  categories?: string[] | null
  locale?: string | null
  // ``private | restricted | public | unlisted`` 전환. system 으로는 못 바꿈
  // (super_user 영역). restricted 전환 시 ACL endpoint 도 함께 호출해야 함.
  visibility?: Exclude<MarketplaceVisibility, 'system'> | null
}

export interface MarketplaceItemACLBody {
  user_ids: string[]
  permission?: 'view' | 'install' | 'manage'
}

// ---------------------------------------------------------------------------
// Catalog filters (query params)
// ---------------------------------------------------------------------------

export interface MarketplaceListFilters {
  resource_type?: MarketplaceResourceType
  q?: string
  visibility?: MarketplaceVisibility[]
  category?: string[]
  installed?: boolean
  install_state?: InstallationStatus
  support_level?: string
  source_kind?: string
  is_listed?: boolean
  limit?: number
  offset?: number
}

// ---------------------------------------------------------------------------
// k-skill sync status (admin)
// ---------------------------------------------------------------------------

export interface KSkillSyncStatusItem {
  id: string
  name: string
  slug: string
  status: MarketplaceStatus
  source_external_id?: string | null
  latest_version_id?: string | null
  updated_at: string
}

export interface KSkillSyncStatus {
  count: number
  last_updated_at: string | null
  items: KSkillSyncStatusItem[]
}
