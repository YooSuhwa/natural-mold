import { apiFetch } from './client'
import type {
  PlatformInstallation,
  PlatformTenant,
  PlatformTenantCreate,
  PlatformTenantCreateResult,
  PlatformTenantDeletion,
  PlatformTenantExport,
  PlatformTenantPage,
  PlatformTenantPatch,
  PlatformTenantQuery,
  PlatformTenantUsage,
} from '@/lib/types/platform-tenant'

const BASE = '/api/admin/tenants'

/**
 * Backend `etag(updated_at)` is the quoted `isoformat()` (`+00:00`), while JSON carries
 * `Z`; echo the server form or the mutation answers `stale_version`.
 */
export function tenantVersionHeader(updatedAt: string): Record<string, string> {
  return { 'If-Match': `"${updatedAt.replace(/Z$/, '+00:00')}"` }
}

function tenantPath(id: string, suffix = ''): string {
  return `${BASE}/${encodeURIComponent(id)}${suffix}`
}

function post<T>(path: string, updatedAt: string | null, body?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: 'POST',
    headers: updatedAt ? tenantVersionHeader(updatedAt) : undefined,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

/** Operator company API (plan 9.7, S03/S04): SUPER and SaaS only, platform scope. */
export const platformTenantsApi = {
  installation: () => apiFetch<PlatformInstallation>('/api/me/installation'),
  list: (query: PlatformTenantQuery = {}) => {
    const params = new URLSearchParams()
    if (query.status) params.set('status', query.status)
    if (query.search) params.set('search', query.search)
    if (query.cursor) params.set('cursor', query.cursor)
    const qs = params.toString()
    return apiFetch<PlatformTenantPage>(qs ? `${BASE}?${qs}` : BASE)
  },
  get: (id: string) => apiFetch<PlatformTenant>(tenantPath(id)),
  usage: (id: string) => apiFetch<PlatformTenantUsage>(tenantPath(id, '/limits')),
  deletion: (id: string) => apiFetch<PlatformTenantDeletion>(tenantPath(id, '/deletion')),
  exports: (id: string) => apiFetch<{ items: PlatformTenantExport[] }>(tenantPath(id, '/exports')),
  exportDownloadPath: (id: string, exportId: string) =>
    tenantPath(id, `/exports/${encodeURIComponent(exportId)}/download`),
  create: (data: PlatformTenantCreate) =>
    apiFetch<PlatformTenantCreateResult>(BASE, { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: PlatformTenantPatch, updatedAt: string) =>
    apiFetch<PlatformTenant>(tenantPath(id), {
      method: 'PATCH',
      headers: tenantVersionHeader(updatedAt),
      body: JSON.stringify(data),
    }),
  suspend: (id: string, updatedAt: string) =>
    post<PlatformTenant>(tenantPath(id, '/suspend'), updatedAt),
  resume: (id: string, updatedAt: string) =>
    post<PlatformTenant>(tenantPath(id, '/resume'), updatedAt),
  startExport: (id: string) => post<{ job_id: string }>(tenantPath(id, '/export'), null),
  scheduleDeletion: (id: string, confirmSlug: string, updatedAt: string) =>
    post<PlatformTenantDeletion>(tenantPath(id, '/schedule-deletion'), updatedAt, {
      confirm_slug: confirmSlug,
    }),
  cancelDeletion: (id: string, updatedAt: string) =>
    post<PlatformTenantDeletion>(tenantPath(id, '/cancel-deletion'), updatedAt),
}
