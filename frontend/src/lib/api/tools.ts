import { apiFetch } from './client'
import type {
  ToolCreateRequest,
  ToolDefinition,
  ToolInstance,
  ToolPatchRequest,
  ToolRunResult,
} from '@/lib/types/tool'

export const toolsApi = {
  listTypes: () => apiFetch<ToolDefinition[]>('/api/tool-types'),
  getType: (key: string) => apiFetch<ToolDefinition>(`/api/tool-types/${key}`),

  list: (params?: { definition_key?: string; enabled?: boolean }) => {
    const search = new URLSearchParams()
    if (params?.definition_key) search.set('definition_key', params.definition_key)
    if (params?.enabled !== undefined) search.set('enabled', String(params.enabled))
    const qs = search.toString()
    return apiFetch<ToolInstance[]>(`/api/tools${qs ? `?${qs}` : ''}`)
  },
  get: (id: string) => apiFetch<ToolInstance>(`/api/tools/${id}`),
  create: (data: ToolCreateRequest) =>
    apiFetch<ToolInstance>('/api/tools', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  update: (id: string, data: ToolPatchRequest) =>
    apiFetch<ToolInstance>(`/api/tools/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  delete: (id: string) => apiFetch<void>(`/api/tools/${id}`, { method: 'DELETE' }),

  run: (id: string, runtime_args: Record<string, unknown> = {}) =>
    apiFetch<ToolRunResult>(`/api/tools/${id}/run`, {
      method: 'POST',
      body: JSON.stringify({ runtime_args }),
    }),
}
