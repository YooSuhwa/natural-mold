import { apiFetch, API_BASE } from './client'
import type {
  Credential,
  CredentialAuditLog,
  CredentialCreateRequest,
  CredentialDefinition,
  CredentialTestResult,
  CredentialUpdateRequest,
  OAuth2AuthStartResponse,
  PreviewTestRequest,
} from '@/lib/types/credential'

export const credentialsApi = {
  listTypes: () => apiFetch<CredentialDefinition[]>('/api/credential-types'),
  getType: (key: string) => apiFetch<CredentialDefinition>(`/api/credential-types/${key}`),

  list: () => apiFetch<Credential[]>('/api/credentials'),
  get: (id: string) => apiFetch<Credential>(`/api/credentials/${id}`),
  create: (data: CredentialCreateRequest) =>
    apiFetch<Credential>('/api/credentials', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  update: (id: string, data: CredentialUpdateRequest) =>
    apiFetch<Credential>(`/api/credentials/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  delete: (id: string) =>
    apiFetch<void>(`/api/credentials/${id}`, { method: 'DELETE' }),

  test: (id: string) =>
    apiFetch<CredentialTestResult>(`/api/credentials/${id}/test`, { method: 'POST' }),
  previewTest: (data: PreviewTestRequest) =>
    apiFetch<CredentialTestResult>('/api/credentials/preview-test', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  listAuditLogs: (id: string, limit = 50) =>
    apiFetch<CredentialAuditLog[]>(
      `/api/credentials/${id}/audit-logs?limit=${limit}`,
    ),

  startOAuth2: (id: string) =>
    apiFetch<OAuth2AuthStartResponse>(`/api/oauth2-credential/auth/${id}`, {
      method: 'POST',
    }),

  oauth2CallbackUrl: () => `${API_BASE}/api/oauth2-credential/callback`,
}

// Operator-managed system credentials (Fix Agent / builder / image gen).
// Same shape as user credentials but separate endpoints + storage flag so
// the user-facing pickers never accidentally surface them.
export const systemCredentialsApi = {
  list: () => apiFetch<Credential[]>('/api/system-credentials'),
  get: (id: string) => apiFetch<Credential>(`/api/system-credentials/${id}`),
  create: (data: CredentialCreateRequest) =>
    apiFetch<Credential>('/api/system-credentials', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  update: (id: string, data: CredentialUpdateRequest) =>
    apiFetch<Credential>(`/api/system-credentials/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  delete: (id: string) =>
    apiFetch<void>(`/api/system-credentials/${id}`, { method: 'DELETE' }),
}
