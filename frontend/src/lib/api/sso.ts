import { API_BASE, ApiError, apiFetch } from '@/lib/api/client'
import { throwApiError } from '@/lib/api/errors'
import type {
  LdapConnectionInput,
  OidcConnectionInput,
  SsoConnection,
  SsoDiscovery,
  SsoMode,
  SsoPolicy,
  SsoTestResponse,
  TenantDomain,
} from '@/lib/types/sso'
import type { AuthResponse } from '@/lib/types/user'

/** Company SSO API (plan 10.10, S30; H-08). */

/** Public, pre-session POST: no CSRF header, no refresh-retry (like /api/auth/login). */
async function publicPost<T>(path: string, body: unknown, headers?: HeadersInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'network_error', 'network error')
  }
  if (!response.ok) await throwApiError(response, 'UNKNOWN_ERROR')
  return response.json() as Promise<T>
}

export const ssoApi = {
  discover: (email: string) => publicPost<SsoDiscovery>('/api/auth/sso/discover', { email }),
  ldapLogin: (connectionId: string, username: string, password: string, loginCsrf: string) =>
    publicPost<AuthResponse>(
      `/api/auth/ldap/${connectionId}/login`,
      { username, password },
      { 'X-Login-CSRF': loginCsrf },
    ),
}

const tenantPath = (tenantId: string, rest: string) => `/api/tenants/${tenantId}${rest}`
const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
})

export const tenantSsoApi = {
  listDomains: (tenantId: string) =>
    apiFetch<{ items: TenantDomain[] }>(tenantPath(tenantId, '/domains')),
  addDomain: (tenantId: string, domain: string) =>
    apiFetch<TenantDomain>(tenantPath(tenantId, '/domains'), json('POST', { domain })),
  verifyDomain: (tenantId: string, id: string) =>
    apiFetch<TenantDomain>(tenantPath(tenantId, `/domains/${id}/verify`), json('POST')),
  removeDomain: (tenantId: string, id: string) =>
    apiFetch<void>(tenantPath(tenantId, `/domains/${id}`), json('DELETE')),
  listConnections: (tenantId: string) =>
    apiFetch<{ items: SsoConnection[] }>(tenantPath(tenantId, '/sso-connections')),
  createConnection: (tenantId: string, input: OidcConnectionInput | LdapConnectionInput) =>
    apiFetch<SsoConnection>(tenantPath(tenantId, '/sso-connections'), json('POST', input)),
  testConnection: (
    tenantId: string,
    id: string,
    account?: { username: string; password: string },
  ) =>
    apiFetch<SsoTestResponse>(
      tenantPath(tenantId, `/sso-connections/${id}/test`),
      json('POST', account ?? {}),
    ),
  activateConnection: (tenantId: string, id: string) =>
    apiFetch<SsoConnection>(tenantPath(tenantId, `/sso-connections/${id}/activate`), json('POST')),
  deleteConnection: (tenantId: string, id: string) =>
    apiFetch<void>(tenantPath(tenantId, `/sso-connections/${id}`), json('DELETE')),
  getPolicy: (tenantId: string) => apiFetch<SsoPolicy>(tenantPath(tenantId, '/sso-policy')),
  putPolicy: (tenantId: string, mode: SsoMode, breakGlass: string[]) =>
    apiFetch<SsoPolicy>(
      tenantPath(tenantId, '/sso-policy'),
      json('PUT', { mode, break_glass_user_ids: breakGlass }),
    ),
}
