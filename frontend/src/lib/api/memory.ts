import { apiFetch } from './client'
import type {
  AgentMemorySettings,
  AgentMemorySettingsUpdate,
  MemoryActivity,
  MemoryBulkApproveResult,
  MemoryDeletionReceipt,
  MemoryExportFormat,
  MemoryPolicy,
  MemoryProfile,
  MemoryProfileKind,
  MemoryProfiles,
  MemoryProfileUpdate,
  MemoryProposal,
  MemoryProposalApproval,
  MemoryProposalCreate,
  MemoryProposalEditApprove,
  MemoryProposalStatusFilter,
  MemoryRecallPreview,
  MemoryRecallPreviewRequest,
  MemoryRecord,
  MemoryRecordCreate,
  MemoryRecordUpdate,
  MemoryRevision,
  MemoryScopeCount,
  MemoryScopeDelete,
  MemoryScopeFilter,
  MemoryScopeQuery,
  MemoryStatusFilter,
  MemoryTenantPolicy,
  MemoryTenantPolicyValues,
  MemoryVectorStatus,
  MemoryVectorUpdate,
  UserMemorySettings,
  UserMemorySettingsUpdate,
} from '@/lib/types/memory'

export interface MemoryListParams {
  scope?: MemoryScopeFilter
  agentId?: string | null
  q?: string | null
  /** Omitted = the backend default (`active`). */
  status?: MemoryStatusFilter
}

function buildMemorySearch(params?: MemoryListParams): string {
  const search = new URLSearchParams()
  if (params?.scope && params.scope !== 'all') search.set('scope', params.scope)
  if (params?.agentId) search.set('agent_id', params.agentId)
  if (params?.q?.trim()) search.set('q', params.q.trim())
  if (params?.status) search.set('status', params.status)
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

function buildScopeSearch(query: MemoryScopeQuery, extra?: Record<string, string>): string {
  const search = new URLSearchParams(extra)
  search.set('scope', query.scope)
  if (query.scope === 'agent' && query.agentId) search.set('agent_id', query.agentId)
  return `?${search.toString()}`
}

/** `GET /api/memories/export` path (a file download, fetched with credentials). */
export function memoryExportPath(format: MemoryExportFormat, query: MemoryScopeQuery): string {
  return `/api/memories/export${buildScopeSearch(query, { format })}`
}

export const memoryApi = {
  getUserSettings: () => apiFetch<UserMemorySettings>('/api/me/memory-settings'),
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
  getPolicy: () => apiFetch<MemoryPolicy>('/api/me/memory-policy'),
  list: (params?: MemoryListParams) =>
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
  delete: (memoryId: string) => apiFetch<void>(`/api/memories/${memoryId}`, { method: 'DELETE' }),
  revoke: (memoryId: string, reason?: string | null) =>
    apiFetch<MemoryRecord>(`/api/memories/${memoryId}/revoke`, {
      method: 'POST',
      body: JSON.stringify(reason ? { reason } : {}),
    }),
  restore: (memoryId: string) =>
    apiFetch<MemoryRecord>(`/api/memories/${memoryId}/restore`, { method: 'POST' }),
  listRevisions: (memoryId: string) =>
    apiFetch<MemoryRevision[]>(`/api/memories/${memoryId}/revisions`),
  countScope: (query: MemoryScopeQuery) =>
    apiFetch<MemoryScopeCount>(`/api/memories/delete-scope/count${buildScopeSearch(query)}`),
  deleteScope: (data: MemoryScopeDelete) =>
    apiFetch<MemoryDeletionReceipt>('/api/memories/delete-scope', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  listActivity: () => apiFetch<MemoryActivity[]>('/api/me/memory-activity'),
  listProposals: (status: MemoryProposalStatusFilter = 'pending') =>
    apiFetch<MemoryProposal[]>(`/api/memory-proposals?status=${status}`),
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
  bulkApproveProposals: (ids: string[]) =>
    apiFetch<MemoryBulkApproveResult>('/api/memory-proposals/bulk-approve', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  rejectProposal: (proposalId: string) =>
    apiFetch<MemoryProposal>(`/api/memory-proposals/${proposalId}/reject`, {
      method: 'POST',
    }),
  editAndApproveProposal: (proposalId: string, data: MemoryProposalEditApprove) =>
    apiFetch<MemoryProposalApproval>(`/api/memory-proposals/${proposalId}/edit-and-approve`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  getProfiles: (agentId?: string | null) =>
    apiFetch<MemoryProfiles>(
      `/api/me/memory-profile${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''}`,
    ),
  updateProfile: (kind: MemoryProfileKind, data: MemoryProfileUpdate) =>
    apiFetch<MemoryProfile>(`/api/me/memory-profile/${kind}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  pin: (memoryId: string) =>
    apiFetch<MemoryProfile>(`/api/memories/${memoryId}/pin`, { method: 'POST' }),
  unpin: (memoryId: string) =>
    apiFetch<MemoryProfile>(`/api/memories/${memoryId}/pin`, { method: 'DELETE' }),
  previewRecall: (data: MemoryRecallPreviewRequest) =>
    apiFetch<MemoryRecallPreview>('/api/me/memory-recall-preview', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  getTenantPolicy: () => apiFetch<MemoryTenantPolicy>('/api/me/memory-tenant-policy'),
  updateTenantPolicy: (data: MemoryTenantPolicyValues) =>
    apiFetch<MemoryTenantPolicy>('/api/me/memory-tenant-policy', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  getVectorStatus: () => apiFetch<MemoryVectorStatus>('/api/me/memory-vector'),
  updateVector: (data: MemoryVectorUpdate) =>
    apiFetch<MemoryVectorStatus>('/api/me/memory-vector', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
}
