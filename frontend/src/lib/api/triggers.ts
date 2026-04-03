import { apiFetch } from './client'
import type { AgentTrigger, TriggerCreateRequest, TriggerUpdateRequest } from '@/lib/types'

export const triggersApi = {
  list: (agentId: string) => apiFetch<AgentTrigger[]>(`/api/agents/${agentId}/triggers`),
  create: (agentId: string, data: TriggerCreateRequest) =>
    apiFetch<AgentTrigger>(`/api/agents/${agentId}/triggers`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  update: (agentId: string, triggerId: string, data: TriggerUpdateRequest) =>
    apiFetch<AgentTrigger>(`/api/agents/${agentId}/triggers/${triggerId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  delete: (agentId: string, triggerId: string) =>
    apiFetch<void>(`/api/agents/${agentId}/triggers/${triggerId}`, {
      method: 'DELETE',
    }),
}
