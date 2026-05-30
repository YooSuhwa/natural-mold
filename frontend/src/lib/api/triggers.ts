import { apiFetch } from './client'
import type {
  AgentTrigger,
  TriggerCreateRequest,
  TriggerRun,
  TriggerSummary,
  TriggerUpdateRequest,
} from '@/lib/types'

export const triggersApi = {
  listAll: () => apiFetch<AgentTrigger[]>('/api/triggers'),
  summary: () => apiFetch<TriggerSummary>('/api/triggers/summary'),
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
  updateGlobal: (triggerId: string, data: TriggerUpdateRequest) =>
    apiFetch<AgentTrigger>(`/api/triggers/${triggerId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteGlobal: (triggerId: string) =>
    apiFetch<void>(`/api/triggers/${triggerId}`, {
      method: 'DELETE',
    }),
  runNow: (triggerId: string) =>
    apiFetch<TriggerRun>(`/api/triggers/${triggerId}/run-now`, {
      method: 'POST',
    }),
  runs: (triggerId: string) => apiFetch<TriggerRun[]>(`/api/triggers/${triggerId}/runs`),
}
