import { apiFetch } from './client'
import type {
  AgentMemorySettings,
  AgentMemorySettingsUpdate,
  MemoryProposal,
  MemoryProposalApproval,
  MemoryProposalCreate,
  MemoryProposalEditApprove,
  MemoryRecord,
  MemoryRecordCreate,
  MemoryRecordUpdate,
  MemoryScopeFilter,
  UserMemorySettings,
  UserMemorySettingsUpdate,
} from '@/lib/types/memory'

function buildMemorySearch(params?: {
  scope?: MemoryScopeFilter
  agentId?: string | null
  q?: string | null
}): string {
  const search = new URLSearchParams()
  if (params?.scope && params.scope !== 'all') search.set('scope', params.scope)
  if (params?.agentId) search.set('agent_id', params.agentId)
  if (params?.q?.trim()) search.set('q', params.q.trim())
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

export const memoryApi = {
  getUserSettings: () =>
    apiFetch<UserMemorySettings>('/api/me/memory-settings'),
  updateUserSettings: (data: UserMemorySettingsUpdate) =>
    apiFetch<UserMemorySettings>('/api/me/memory-settings', {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  getAgentSettings: (agentId: string) =>
    apiFetch<AgentMemorySettings>(`/api/agents/${agentId}/memory-settings`),
  updateAgentSettings: (agentId: string, data: AgentMemorySettingsUpdate) =>
    apiFetch<AgentMemorySettings>(`/api/agents/${agentId}/memory-settings`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  list: (params?: { scope?: MemoryScopeFilter; agentId?: string | null; q?: string | null }) =>
    apiFetch<MemoryRecord[]>(`/api/memories${buildMemorySearch(params)}`),
  create: (data: MemoryRecordCreate) =>
    apiFetch<MemoryRecord>('/api/memories', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  update: (memoryId: string, data: MemoryRecordUpdate) =>
    apiFetch<MemoryRecord>(`/api/memories/${memoryId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  delete: (memoryId: string) =>
    apiFetch<void>(`/api/memories/${memoryId}`, { method: 'DELETE' }),
  createProposal: (data: MemoryProposalCreate) =>
    apiFetch<MemoryProposal>('/api/memory-proposals', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  getProposal: (proposalId: string) =>
    apiFetch<MemoryProposal>(`/api/memory-proposals/${proposalId}`),
  approveProposal: (proposalId: string) =>
    apiFetch<MemoryProposalApproval>(`/api/memory-proposals/${proposalId}/approve`, {
      method: 'POST',
    }),
  rejectProposal: (proposalId: string) =>
    apiFetch<MemoryProposal>(`/api/memory-proposals/${proposalId}/reject`, {
      method: 'POST',
    }),
  editAndApproveProposal: (proposalId: string, data: MemoryProposalEditApprove) =>
    apiFetch<MemoryProposalApproval>(
      `/api/memory-proposals/${proposalId}/edit-and-approve`,
      {
        method: 'POST',
        body: JSON.stringify(data),
      },
    ),
}
