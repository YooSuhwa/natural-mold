import { apiFetch } from './client'
import type { TenantOverview } from '@/lib/types/organization'
import type {
  PublicationRequestItem,
  TenantInvitation,
  TenantInvitationCreate,
  TenantInvitationCreated,
  TenantMember,
  TenantPage,
  TenantPolicies,
  TenantPoliciesUpdate,
  TenantRole,
} from '@/lib/types/tenant-admin'

/**
 * Company writes compare `If-Match` with `etag(tenant.updated_at)` as an exact string,
 * which is Python's `+00:00` ISO form, while response bodies serialize UTC as `Z`.
 */
export function tenantVersionHeader(updatedAt: string): Record<string, string> {
  return { 'If-Match': `"${updatedAt.replace(/Z$/, '+00:00')}"` }
}

export function tenantPath(tenantId: string, rest = ''): string {
  return `/api/tenants/${encodeURIComponent(tenantId)}${rest}`
}

export function cursorQuery(cursor?: string | null, limit?: number): string {
  const params = new URLSearchParams()
  if (cursor) params.set('cursor', cursor)
  if (limit !== undefined) params.set('limit', String(limit))
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

function write(method: string, version: string | null, body?: unknown): RequestInit {
  return {
    method,
    headers: version ? tenantVersionHeader(version) : undefined,
    body: body === undefined ? undefined : JSON.stringify(body),
  }
}

/** S05 company profile and policies, S06 members and invitations (B-11, E-09). */
export const tenantAdminApi = {
  rename: (tenantId: string, name: string, version: string) =>
    apiFetch<TenantOverview>(tenantPath(tenantId), write('PATCH', version, { name })),

  getPolicies: (tenantId: string) => apiFetch<TenantPolicies>(tenantPath(tenantId, '/policies')),
  putPolicies: (tenantId: string, data: TenantPoliciesUpdate, version: string) =>
    apiFetch<TenantPolicies>(tenantPath(tenantId, '/policies'), write('PUT', version, data)),

  listPublicationRequests: (tenantId: string, cursor?: string | null) =>
    apiFetch<TenantPage<PublicationRequestItem>>(
      tenantPath(tenantId, `/publication-requests${cursorQuery(cursor)}`),
    ),
  approvePublication: (tenantId: string, itemId: string) =>
    apiFetch<unknown>(
      tenantPath(tenantId, `/publication-requests/${encodeURIComponent(itemId)}/approve`),
      write('POST', null),
    ),
  rejectPublication: (tenantId: string, itemId: string, note: string) =>
    apiFetch<unknown>(
      tenantPath(tenantId, `/publication-requests/${encodeURIComponent(itemId)}/reject`),
      write('POST', null, { note }),
    ),

  listMembers: (tenantId: string, cursor?: string | null) =>
    apiFetch<TenantPage<TenantMember>>(tenantPath(tenantId, `/members${cursorQuery(cursor)}`)),
  setMemberStatus: (
    tenantId: string,
    userId: string,
    status: 'active' | 'suspended',
    version: string,
  ) =>
    apiFetch<TenantMember>(
      tenantPath(tenantId, `/members/${encodeURIComponent(userId)}`),
      write('PATCH', version, { status }),
    ),
  removeMember: (tenantId: string, userId: string, version: string) =>
    apiFetch<void>(
      tenantPath(tenantId, `/members/${encodeURIComponent(userId)}`),
      write('DELETE', version),
    ),
  replaceRoles: (tenantId: string, userId: string, roles: TenantRole[], version: string) =>
    apiFetch<{ user_id: string; roles: TenantRole[] }>(
      tenantPath(tenantId, `/roles/${encodeURIComponent(userId)}`),
      write('PUT', version, { roles }),
    ),

  listInvitations: (tenantId: string, cursor?: string | null) =>
    apiFetch<TenantPage<TenantInvitation>>(
      tenantPath(tenantId, `/invitations${cursorQuery(cursor)}`),
    ),
  createInvitation: (tenantId: string, data: TenantInvitationCreate, version: string) =>
    apiFetch<TenantInvitationCreated>(
      tenantPath(tenantId, '/invitations'),
      write('POST', version, data),
    ),
  revokeInvitation: (tenantId: string, invitationId: string, version: string) =>
    apiFetch<void>(
      tenantPath(tenantId, `/invitations/${encodeURIComponent(invitationId)}`),
      write('DELETE', version),
    ),
  resendInvitation: (tenantId: string, invitationId: string, version: string) =>
    apiFetch<TenantInvitationCreated>(
      tenantPath(tenantId, `/invitations/${encodeURIComponent(invitationId)}/resend`),
      write('POST', version),
    ),
}
