import { apiFetch } from './client'
import { organizationVersionHeader } from './organizations'
import type { DirectoryPage, DirectoryQuery } from '@/lib/types/organization'
import type {
  CatalogImpactView,
  CatalogKind,
  CatalogOverride,
  CatalogPolicyView,
  InvitationCreate,
  InvitationCreated,
  InvitationView,
  OrgRetentionUpdate,
  OrgRetentionView,
  OrgRetentionWrite,
  UsageLimitView,
} from '@/lib/types/org-governance'

function orgPath(orgId: string): string {
  return `/api/orgs/${encodeURIComponent(orgId)}`
}

function pageQuery(query: DirectoryQuery): string {
  const params = new URLSearchParams()
  if (query.cursor) params.set('cursor', query.cursor)
  if (query.limit) params.set('limit', String(query.limit))
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

/**
 * Organization governance (plan 9.7, S08–S11). Versioned writes echo the
 * organization version (`OrganizationView.updated_at`): retention, limits and
 * catalog policy are all stored on the organization row.
 */
export const orgGovernanceApi = {
  getRetention: (orgId: string) => apiFetch<OrgRetentionView>(`${orgPath(orgId)}/retention`),
  putRetention: (orgId: string, data: OrgRetentionUpdate, version: string) =>
    apiFetch<OrgRetentionWrite>(`${orgPath(orgId)}/retention`, {
      method: 'PUT',
      headers: organizationVersionHeader(version),
      body: JSON.stringify(data),
    }),

  getLimits: (orgId: string) => apiFetch<UsageLimitView>(`${orgPath(orgId)}/limits`),
  putMemberDefault: (orgId: string, defaultMemberLimit: string | null, version: string) =>
    apiFetch<UsageLimitView>(`${orgPath(orgId)}/limits`, {
      method: 'PUT',
      headers: organizationVersionHeader(version),
      body: JSON.stringify({ default_member_limit: defaultMemberLimit }),
    }),
  getUserLimit: (orgId: string, userId: string) => {
    const params = new URLSearchParams({ scope: 'user', id: userId, org_id: orgId })
    return apiFetch<UsageLimitView>(`/api/usage-limits?${params.toString()}`)
  },
  putUserLimit: (orgId: string, userId: string, limit: string | null, version: string) => {
    const params = new URLSearchParams({ scope: 'user', id: userId, org_id: orgId })
    return apiFetch<UsageLimitView>(`/api/usage-limits?${params.toString()}`, {
      method: 'PUT',
      headers: organizationVersionHeader(version),
      body: JSON.stringify({ monthly_spend_limit: limit }),
    })
  },

  getCatalog: (orgId: string) => apiFetch<CatalogPolicyView>(`${orgPath(orgId)}/catalog-policy`),
  putCatalog: (orgId: string, items: CatalogOverride[], version: string) =>
    apiFetch<CatalogPolicyView>(`${orgPath(orgId)}/catalog-policy`, {
      method: 'PUT',
      headers: organizationVersionHeader(version),
      body: JSON.stringify({ items }),
    }),
  catalogImpact: (orgId: string, type: CatalogKind, id: string) => {
    const params = new URLSearchParams({ type, id })
    return apiFetch<CatalogImpactView>(
      `${orgPath(orgId)}/catalog-policy/impact?${params.toString()}`,
    )
  },

  listInvitations: (orgId: string, query: DirectoryQuery = {}) =>
    apiFetch<DirectoryPage<InvitationView>>(`${orgPath(orgId)}/invitations${pageQuery(query)}`),
  createInvitation: (orgId: string, data: InvitationCreate) =>
    apiFetch<InvitationCreated>(`${orgPath(orgId)}/invitations`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  revokeInvitation: (orgId: string, invitationId: string) =>
    apiFetch<void>(`${orgPath(orgId)}/invitations/${encodeURIComponent(invitationId)}`, {
      method: 'DELETE',
    }),
  resendInvitation: (orgId: string, invitationId: string) =>
    apiFetch<InvitationCreated>(
      `${orgPath(orgId)}/invitations/${encodeURIComponent(invitationId)}/resend`,
      { method: 'POST' },
    ),
}
