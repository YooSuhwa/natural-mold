import { apiFetch } from './client'
import type { Tool, ToolCustomCreateRequest } from '@/lib/types'

export const toolsApi = {
  list: () => apiFetch<Tool[]>('/api/tools'),
  createCustom: (data: ToolCustomCreateRequest) =>
    apiFetch<Tool>('/api/tools/custom', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: { connection_id?: string | null }) =>
    apiFetch<Tool>(`/api/tools/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  delete: (id: string) => apiFetch<void>(`/api/tools/${id}`, { method: 'DELETE' }),
}
