import { apiFetch } from './client'
import type {
  Provider,
  ProviderCreateRequest,
  ProviderUpdateRequest,
  ProviderTestResponse,
  DiscoveredModel,
} from '@/lib/types'

export const providersApi = {
  list: () => apiFetch<Provider[]>('/api/providers'),
  create: (data: ProviderCreateRequest) =>
    apiFetch<Provider>('/api/providers', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: ProviderUpdateRequest) =>
    apiFetch<Provider>(`/api/providers/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  delete: (id: string) => apiFetch<void>(`/api/providers/${id}`, { method: 'DELETE' }),
  test: (id: string) =>
    apiFetch<ProviderTestResponse>(`/api/providers/${id}/test`, { method: 'POST' }),
  discoverModels: (id: string) =>
    apiFetch<DiscoveredModel[]>(`/api/providers/${id}/discover-models`),
}
