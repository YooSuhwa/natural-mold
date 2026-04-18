import { apiFetch } from './client'
import type {
  Connection,
  ConnectionCreateRequest,
  ConnectionType,
  ConnectionUpdateRequest,
} from '@/lib/types'

export interface ListConnectionsParams {
  type?: ConnectionType
  provider_name?: string
}

function buildQuery(params: ListConnectionsParams): string {
  const search = new URLSearchParams()
  if (params.type) search.set('type', params.type)
  if (params.provider_name) search.set('provider_name', params.provider_name)
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

export const connectionsApi = {
  list: (params: ListConnectionsParams = {}) =>
    apiFetch<Connection[]>(`/api/connections${buildQuery(params)}`),
  get: (id: string) => apiFetch<Connection>(`/api/connections/${id}`),
  create: (data: ConnectionCreateRequest) =>
    apiFetch<Connection>('/api/connections', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  update: (id: string, data: ConnectionUpdateRequest) =>
    apiFetch<Connection>(`/api/connections/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  delete: (id: string) =>
    apiFetch<void>(`/api/connections/${id}`, { method: 'DELETE' }),
}
