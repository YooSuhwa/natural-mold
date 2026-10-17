/**
 * Organization governance screens (plan 13.14 S08–S11): retention, usage limits,
 * catalog policy and invitations. Mirrors `app/schemas/{retention,usage_limits,
 * org_catalog,org_invitations}.py`. Decimal amounts arrive as strings.
 */

/** `GET /api/orgs/{org_id}/retention`. `conversation_days: null` = unlimited. */
export interface OrgRetention {
  conversation_days: number | null
  agent_trash_days: number
  removed_member_days: number
}

export interface OrgRetentionView extends OrgRetention {
  updated_at: string
}

export interface OrgRetentionUpdate extends OrgRetention {
  /** Shortening a period needs a second request with `confirm: true`. */
  confirm?: boolean
}

export interface RetentionPreview {
  conversations: number
  agents: number
  removed_member_conversations: number
  removed_member_memories: number
  audit_events: number
}

/** `saved: false` + `preview`: nothing changed, resend with `confirm: true`. */
export interface OrgRetentionWrite {
  saved: boolean
  preview: RetentionPreview | null
  policy: OrgRetentionView
}

export type LimitScope = 'tenant' | 'org' | 'user' | 'agent'

/** Backend `UsageLimitView`; amounts are decimal strings. */
export interface UsageLimitView {
  scope: LimitScope
  id: string
  org_id: string | null
  monthly_spend_limit: string | null
  effective_limit: string | null
  used: string
  remaining: string | null
  resets_at: string
  parent_limit: string | null
  allocated: string | null
  default_member_limit: string | null
}

export type CatalogKind = 'model' | 'tool' | 'skill' | 'mcp_server'

export interface CatalogItemView {
  type: CatalogKind
  id: string
  name: string
  source: 'platform' | 'company'
  company_enabled: boolean
  organization_enabled: boolean
  enabled: boolean
}

export interface CatalogPolicyView {
  items: CatalogItemView[]
  updated_at: string
}

export interface CatalogOverride {
  type: CatalogKind
  id: string
  enabled: boolean
}

export interface CatalogImpactView {
  type: CatalogKind
  id: string
  affected_agents: number
  required_agents: number
}

export type InvitationRole = 'member' | 'builder' | 'admin' | 'auditor'
export type InvitationStatus = 'pending' | 'accepted' | 'revoked' | 'expired'

export interface InvitationView {
  id: string
  email: string
  role: InvitationRole
  group_ids: string[]
  expires_at: string
  created_at: string
  status: InvitationStatus
}

/** `token` is a one-time secret (null when an identical pending invite was reused). */
export interface InvitationCreated extends InvitationView {
  token: string | null
}

export interface InvitationCreate {
  email: string
  role?: InvitationRole | null
  group_ids?: string[]
}
