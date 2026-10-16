import { apiFetch } from './client'
import type {
  ActiveOrganizationResponse,
  OrganizationSummary,
  TenantOrganizationArchive,
  TenantOrganizationCreate,
  TenantOrganizationCreateResult,
  TenantOrganizationPage,
  TenantOrganizationPageQuery,
  TenantOrganizationRename,
  TenantOrganizationSummary,
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

export const tenantOrganizationsApi = {
  list: (tenantId: string, query: TenantOrganizationPageQuery = {}) => {
    const params = new URLSearchParams()
    if (query.cursor) params.set('cursor', query.cursor)
    if (query.limit !== undefined) params.set('limit', String(query.limit))
    const qs = params.toString()
    return apiFetch<TenantOrganizationPage>(
      `${tenantOrganizationsPath(tenantId)}${qs ? `?${qs}` : ''}`,
    )
  },
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
