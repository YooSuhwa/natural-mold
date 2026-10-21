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
  /** `personal`: the owner's own company from a personal-tenant signup. */
  tenant_kind?: 'company' | 'personal'
  is_default: boolean
  /** True for the org the server resolves for this browser's active-org cookie. */
  is_active: boolean
  roles: OrgRole[]
  capabilities: OrgCapability[]
  /** False while capabilities are not enforced (legacy/shadow): everyone may create (H-05). */
  capabilities_enforced?: boolean
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
  view: 'admin'
  items: TenantOrganizationSummary[]
  next_cursor: string | null
  has_more: boolean
}

export interface TenantOrganizationAuditorPage {
  view: 'auditor'
  items: TenantOrganizationAuditorSummary[]
  next_cursor: string | null
  has_more: boolean
}

export type TenantOrganizationPage = TenantOrganizationAdminPage | TenantOrganizationAuditorPage

export interface TenantOrganizationPageQuery {
  cursor?: string | null
  limit?: number
}

export type InvitationRole = 'member' | 'builder' | 'admin' | 'auditor'
export type InvitationStatus = 'pending' | 'accepted' | 'revoked' | 'expired'

/** Backend `InvitationCreated`. `token` is a one-time secret: never log or persist it. */
export interface TenantOrganizationInvitation {
  id: string
  email: string
  role: InvitationRole
  group_ids: string[]
  expires_at: string
  created_at: string
  status: InvitationStatus
  token: string | null
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
  return page.view === 'admin'
}

// ----------------------------- organization administration -----------------------------

/** Backend `OrgControlPermission`. */
export type OrgControlPermission = 'can_manage_members' | 'can_manage_policy'
export type RegistrationPolicy = 'default_org' | 'invite_only' | 'closed'

export interface OrganizationSettings {
  default_member_role: DefaultMemberRole
  allow_public_share_links: boolean
  registration_policy: RegistrationPolicy | null
}

/** `GET /api/orgs/{org_id}`. `updated_at` is the org version every org write must echo. */
export interface OrganizationView {
  id: string
  tenant_id: string
  name: string
  slug: string
  status: OrganizationStatus
  is_default: boolean
  settings: OrganizationSettings
  updated_at: string
  permissions: OrgControlPermission[]
}

export interface OrganizationPatch {
  name?: string
  settings?: Partial<OrganizationSettings>
}

export interface DirectoryQuery {
  cursor?: string | null
  limit?: number
}

/** Backend `DirectoryPage[...]`; `updated_at` is the current org version. */
/** Role/capability list query; `principal` narrows to one principal's full grant set. */
export interface GrantDirectoryQuery extends DirectoryQuery {
  principal?: Principal | null
}

/** `GET /api/tenants/{tenant_id}` (subset). `access` is the caller's company authority. */
export interface TenantOverview {
  id: string
  name: string
  slug: string
  status: TenantStatus
  updated_at: string
  access: 'member' | 'auditor' | 'admin'
  /** Company admins and auditors only. */
  active_member_count?: number
}

export interface DirectoryPage<Entry> {
  items: Entry[]
  next_cursor: string | null
  has_more: boolean
  updated_at: string
}

export type MemberStatus = 'active' | 'suspended' | 'removed'

export interface MemberView {
  user_id: string
  display_name: string
  email: string | null
  status: MemberStatus
  joined_at: string
  removed_at: string | null
}

export interface GroupView {
  id: string
  org_id: string
  name: string
  description: string | null
  source: 'manual' | 'idp'
  created_at: string
  updated_at: string
}

export interface GroupCreate {
  name: string
  description?: string | null
}

export interface GroupPatch {
  name?: string
  description?: string | null
}

export interface GroupMemberView {
  user_id: string
  display_name: string
  email: string | null
  added_at: string
}

export type RolePrincipal = { type: 'user'; id: string } | { type: 'group'; id: string }
export type Principal = RolePrincipal | { type: 'organization'; id: string }

export interface RoleGrantView {
  id: string
  principal: RolePrincipal
  /** Profile name (users) or group name; absent on older payloads. */
  principal_name?: string | null
  role: OrgRole
  granted_by: string | null
  granted_at: string
}

export interface RoleAssignment {
  principal: RolePrincipal
  roles: OrgRole[]
}

export interface CapabilityGrantView {
  id: string
  principal: Principal
  principal_name?: string | null
  capability: OrgCapability
  granted_by: string | null
  granted_at: string
}

export interface CapabilityAssignment {
  principal: Principal
  capabilities: OrgCapability[]
}

export type PrincipalSearchType = 'user' | 'group' | 'org'

export interface PrincipalSearchView {
  type: PrincipalSearchType
  id: string
  display_name: string
  email: string | null
  member_count: number | null
}
