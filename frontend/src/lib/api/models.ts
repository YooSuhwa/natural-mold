import { apiFetch } from './client'
import type { Model, ModelCreateRequest, ModelUpdateRequest } from '@/lib/types'

export const modelsApi = {
  list: () => apiFetch<Model[]>('/api/models'),
  create: (data: ModelCreateRequest) =>
    apiFetch<Model>('/api/models', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: ModelUpdateRequest) =>
    apiFetch<Model>(`/api/models/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  delete: (id: string) => apiFetch<void>(`/api/models/${id}`, { method: 'DELETE' }),
}
