import { apiFetch } from './client'
import type {
  Credential,
  CredentialCreateRequest,
  CredentialUpdateRequest,
  CredentialProviderDef,
  CredentialUsage,
} from '@/lib/types'

export const credentialsApi = {
  list: () => apiFetch<Credential[]>('/api/credentials'),
  create: (data: CredentialCreateRequest) =>
    apiFetch<Credential>('/api/credentials', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: CredentialUpdateRequest) =>
    apiFetch<Credential>(`/api/credentials/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  delete: (id: string) => apiFetch<void>(`/api/credentials/${id}`, { method: 'DELETE' }),
  getProviders: () => apiFetch<CredentialProviderDef[]>('/api/credentials/providers'),
  getUsage: (id: string) => apiFetch<CredentialUsage>(`/api/credentials/${id}/usage`),
}
