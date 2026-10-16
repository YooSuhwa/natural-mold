/**
 * Organization types — mirror backend `app/schemas/organization.py`
 * (self-service navigation) and `app/schemas/tenant_organizations.py` /
 * `tenant_organization_lifecycle.py` (company administration).
 */

export type OrgRole = 'admin' | 'builder' | 'auditor'

export type OrgCapability =
  | 'cap_create_agent'
  | 'cap_create_skill'
  | 'cap_create_tool'
  | 'cap_register_mcp'
  | 'cap_connect_database'
  | 'cap_share_org_wide'
  | 'cap_publish_marketplace'

export type TenantStatus = 'active' | 'suspended' | 'deletion_scheduled'

/** `GET /api/me/organizations` item. */
export interface OrganizationSummary {
  id: string
  name: string
  slug: string
  tenant_id: string
  tenant_name: string
  tenant_status: TenantStatus
  is_default: boolean
  roles: OrgRole[]
  capabilities: OrgCapability[]
}

/** `POST /api/me/active-org` body. */
export interface ActiveOrganizationRequest {
  org_id: string
}

/** `POST /api/me/active-org` response. */
export interface ActiveOrganizationResponse {
  org_id: string
  tenant_id: string
}

// ----------------------------- company administration -----------------------------

export type OrganizationStatus = 'active' | 'suspended' | 'archived'
export type DefaultMemberRole = 'member' | 'builder'

/** Exactly one of `user_id` / `email` (backend validator `exactly_one_identity`). */
export type TenantOrganizationAdministrator =
  | { user_id: string; email?: never }
  | { email: string; user_id?: never }

export interface TenantOrganizationCreate {
  name: string
  default_member_role?: DefaultMemberRole
  administrator?: TenantOrganizationAdministrator | null
}

export interface TenantOrganizationRename {
  name: string
}

export interface TenantOrganizationArchive {
  /** Exact, unstripped stored organization name. */
  confirm_name: string
}

/** Admin projection. `updated_at` doubles as the optimistic-concurrency version. */
export interface TenantOrganizationSummary {
  id: string
  name: string
  slug: string
  status: OrganizationStatus
  is_default: boolean
  default_member_role: DefaultMemberRole
  member_count: number
  administrator_user_id: string | null
  updated_at: string
}

export interface TenantOrganizationAuditorSummary {
  id: string
  name: string
  member_count: number
}

export interface TenantOrganizationAdminPage {
  items: TenantOrganizationSummary[]
  next_cursor: string | null
  has_more: boolean
}

export interface TenantOrganizationAuditorPage {
  items: TenantOrganizationAuditorSummary[]
  next_cursor: string | null
  has_more: boolean
}

export type TenantOrganizationPage = TenantOrganizationAdminPage | TenantOrganizationAuditorPage

export interface TenantOrganizationPageQuery {
  cursor?: string | null
  limit?: number
}

/** Mirrors backend `InvitationCreated` loosely: only the fields the UI reads are typed. */
export interface TenantOrganizationInvitation {
  token: string | null
  [key: string]: unknown
}

export type TenantOrganizationAdministratorResult =
  | { kind: 'none' }
  | { kind: 'member'; user_id: string }
  | { kind: 'invitation'; invitation: TenantOrganizationInvitation }

export interface TenantOrganizationCreateResult {
  organization: TenantOrganizationSummary
  administrator: TenantOrganizationAdministratorResult
}

/** Narrow a directory page to the admin projection (auditors get id/name/count only). */
export function isTenantOrganizationAdminPage(
  page: TenantOrganizationPage,
): page is TenantOrganizationAdminPage {
  return page.items.every((item) => 'status' in item)
}
