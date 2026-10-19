import { apiFetch } from './client'
import type {
  ActiveOrganizationResponse,
  CapabilityAssignment,
  CapabilityGrantView,
  DirectoryPage,
  DirectoryQuery,
  GroupCreate,
  GroupMemberView,
  GroupPatch,
  GrantDirectoryQuery,
  GroupView,
  MemberStatus,
  MemberView,
  OrganizationPatch,
  OrganizationSummary,
  OrganizationView,
  PrincipalSearchType,
  PrincipalSearchView,
  RoleAssignment,
  RoleGrantView,
  TenantOrganizationArchive,
  TenantOrganizationCreate,
  TenantOrganizationCreateResult,
  TenantOrganizationPage,
  TenantOrganizationPageQuery,
  TenantOrganizationRename,
  TenantOrganizationSummary,
  TenantOverview,
} from '@/lib/types/organization'

/**
 * Backend ``etag(updated_at)`` is the quoted ISO timestamp; mutations must echo
 * it as ``If-Match`` or the server answers ``stale_version``.
 */
export function organizationVersionHeader(updatedAt: string): Record<string, string> {
  return { 'If-Match': `"${updatedAt}"` }
}

function tenantOrganizationsPath(tenantId: string): string {
  return `/api/tenants/${encodeURIComponent(tenantId)}/organizations`
}

function tenantOrganizationPath(tenantId: string, orgId: string): string {
  return `${tenantOrganizationsPath(tenantId)}/${encodeURIComponent(orgId)}`
}

export const meOrganizationsApi = {
  list: () => apiFetch<OrganizationSummary[]>('/api/me/organizations'),
  setActive: (orgId: string) =>
    apiFetch<ActiveOrganizationResponse>('/api/me/active-org', {
      method: 'POST',
      body: JSON.stringify({ org_id: orgId }),
    }),
}

export const tenantsApi = {
  overview: (tenantId: string) =>
    apiFetch<TenantOverview>(`/api/tenants/${encodeURIComponent(tenantId)}`),
}

export const tenantOrganizationsApi = {
  list: (tenantId: string, query: TenantOrganizationPageQuery = {}) =>
    apiFetch<TenantOrganizationPage>(
      `${tenantOrganizationsPath(tenantId)}${pageQueryString(query)}`,
    ),
  create: (tenantId: string, data: TenantOrganizationCreate) =>
    apiFetch<TenantOrganizationCreateResult>(tenantOrganizationsPath(tenantId), {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  rename: (tenantId: string, orgId: string, data: TenantOrganizationRename, updatedAt: string) =>
    apiFetch<TenantOrganizationSummary>(tenantOrganizationPath(tenantId, orgId), {
      method: 'PATCH',
      headers: organizationVersionHeader(updatedAt),
      body: JSON.stringify(data),
    }),
  archive: (tenantId: string, orgId: string, data: TenantOrganizationArchive, updatedAt: string) =>
    apiFetch<TenantOrganizationSummary>(`${tenantOrganizationPath(tenantId, orgId)}/archive`, {
      method: 'POST',
      headers: organizationVersionHeader(updatedAt),
      body: JSON.stringify(data),
    }),
  restore: (tenantId: string, orgId: string, updatedAt: string) =>
    apiFetch<TenantOrganizationSummary>(`${tenantOrganizationPath(tenantId, orgId)}/restore`, {
      method: 'POST',
      headers: organizationVersionHeader(updatedAt),
    }),
}

function pageQueryString(query: DirectoryQuery | GrantDirectoryQuery): string {
  const params = new URLSearchParams()
  if ('principal' in query && query.principal) {
    params.set('principal_type', query.principal.type)
    params.set('principal_id', query.principal.id)
  }
  if (query.cursor) params.set('cursor', query.cursor)
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

function orgPath(orgId: string): string {
  return `/api/orgs/${encodeURIComponent(orgId)}`
}

/**
 * Organization administration (`/api/orgs/{org_id}/...`). Every write echoes the
 * organization version (`OrganizationView.updated_at` / `DirectoryPage.updated_at`).
 */
export const orgAdminApi = {
  get: (orgId: string) => apiFetch<OrganizationView>(orgPath(orgId)),
  update: (orgId: string, data: OrganizationPatch, version: string) =>
    apiFetch<OrganizationView>(orgPath(orgId), {
      method: 'PATCH',
      headers: organizationVersionHeader(version),
      body: JSON.stringify(data),
    }),

  listMembers: (orgId: string, query: DirectoryQuery = {}) =>
    apiFetch<DirectoryPage<MemberView>>(`${orgPath(orgId)}/members${pageQueryString(query)}`),
  addMember: (orgId: string, userId: string, version: string) =>
    apiFetch<MemberView>(`${orgPath(orgId)}/members`, {
      method: 'POST',
      headers: organizationVersionHeader(version),
      body: JSON.stringify({ user_id: userId }),
    }),
  setMemberStatus: (
    orgId: string,
    userId: string,
    status: Exclude<MemberStatus, 'removed'>,
    version: string,
  ) =>
    apiFetch<MemberView>(`${orgPath(orgId)}/members/${encodeURIComponent(userId)}`, {
      method: 'PATCH',
      headers: organizationVersionHeader(version),
      body: JSON.stringify({ status }),
    }),
  removeMember: (orgId: string, userId: string, version: string) =>
    apiFetch<void>(`${orgPath(orgId)}/members/${encodeURIComponent(userId)}`, {
      method: 'DELETE',
      headers: organizationVersionHeader(version),
    }),

  listGroups: (orgId: string, query: DirectoryQuery = {}) =>
    apiFetch<DirectoryPage<GroupView>>(`${orgPath(orgId)}/groups${pageQueryString(query)}`),
  createGroup: (orgId: string, data: GroupCreate, version: string) =>
    apiFetch<GroupView>(`${orgPath(orgId)}/groups`, {
      method: 'POST',
      headers: organizationVersionHeader(version),
      body: JSON.stringify(data),
    }),
  updateGroup: (orgId: string, groupId: string, data: GroupPatch, version: string) =>
    apiFetch<GroupView>(`${orgPath(orgId)}/groups/${encodeURIComponent(groupId)}`, {
      method: 'PATCH',
      headers: organizationVersionHeader(version),
      body: JSON.stringify(data),
    }),
  deleteGroup: (orgId: string, groupId: string, version: string) =>
    apiFetch<void>(`${orgPath(orgId)}/groups/${encodeURIComponent(groupId)}`, {
      method: 'DELETE',
      headers: organizationVersionHeader(version),
    }),
  listGroupMembers: (orgId: string, groupId: string, query: DirectoryQuery = {}) =>
    apiFetch<DirectoryPage<GroupMemberView>>(
      `${orgPath(orgId)}/groups/${encodeURIComponent(groupId)}/members${pageQueryString(query)}`,
    ),
  addGroupMember: (orgId: string, groupId: string, userId: string, version: string) =>
    apiFetch<GroupMemberView>(`${orgPath(orgId)}/groups/${encodeURIComponent(groupId)}/members`, {
      method: 'POST',
      headers: organizationVersionHeader(version),
      body: JSON.stringify({ user_id: userId }),
    }),
  removeGroupMember: (orgId: string, groupId: string, userId: string, version: string) =>
    apiFetch<void>(
      `${orgPath(orgId)}/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(userId)}`,
      { method: 'DELETE', headers: organizationVersionHeader(version) },
    ),

  listRoles: (orgId: string, query: GrantDirectoryQuery = {}) =>
    apiFetch<DirectoryPage<RoleGrantView>>(`${orgPath(orgId)}/roles${pageQueryString(query)}`),
  replaceRoles: (orgId: string, data: RoleAssignment, version: string) =>
    apiFetch<DirectoryPage<RoleGrantView>>(`${orgPath(orgId)}/roles`, {
      method: 'PUT',
      headers: organizationVersionHeader(version),
      body: JSON.stringify(data),
    }),
  listCapabilities: (orgId: string, query: GrantDirectoryQuery = {}) =>
    apiFetch<DirectoryPage<CapabilityGrantView>>(
      `${orgPath(orgId)}/capabilities${pageQueryString(query)}`,
    ),
  replaceCapabilities: (orgId: string, data: CapabilityAssignment, version: string) =>
    apiFetch<DirectoryPage<CapabilityGrantView>>(`${orgPath(orgId)}/capabilities`, {
      method: 'PUT',
      headers: organizationVersionHeader(version),
      body: JSON.stringify(data),
    }),

  searchPrincipals: (orgId: string, q: string, types: readonly PrincipalSearchType[]) => {
    const params = new URLSearchParams({ q, types: types.join(',') })
    return apiFetch<PrincipalSearchView[]>(`${orgPath(orgId)}/principals?${params.toString()}`)
  },
}
