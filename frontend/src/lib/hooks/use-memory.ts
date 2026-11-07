'use client'

import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'

import { ApiError } from '@/lib/api/client'
import { memoryApi, memoryExportPath, type MemoryListParams } from '@/lib/api/memory'
import { downloadApiFile } from '@/lib/hooks/use-api-object-url'
import type {
  AgentMemorySettingsUpdate,
  MemoryExportFormat,
  MemoryProfileKind,
  MemoryProfileUpdate,
  MemoryProposalEditApprove,
  MemoryProposalStatusFilter,
  MemoryRecallPreviewRequest,
  MemoryRecordCreate,
  MemoryRecordUpdate,
  MemoryScopeDelete,
  MemoryScopeQuery,
  UserMemorySettingsUpdate,
} from '@/lib/types/memory'

export const memoryKeys = {
  all: ['memory'] as const,
  settings: ['memory', 'settings'] as const,
  userSettings: ['memory', 'settings', 'user'] as const,
  agentSettings: (agentId: string) => ['memory', 'settings', 'agent', agentId] as const,
  proposal: (proposalId: string) => ['memory', 'proposal', proposalId] as const,
  policy: ['memory', 'policy'] as const,
  list: (params?: MemoryListParams) => ['memory', 'records', params ?? {}] as const,
  revisions: (memoryId: string) => ['memory', 'revisions', memoryId] as const,
  activity: ['memory', 'activity'] as const,
  proposals: (status: MemoryProposalStatusFilter) => ['memory', 'proposals', status] as const,
  profilesAll: ['memory', 'profiles'] as const,
  profiles: (agentId: string | null) => ['memory', 'profiles', agentId] as const,
  scopeCount: (query: MemoryScopeQuery) =>
    ['memory', 'scope-count', query.scope, query.agentId ?? null] as const,
}

function invalidateMemory(qc: QueryClient) {
  qc.invalidateQueries({ queryKey: memoryKeys.all })
}

export function useUserMemorySettings() {
  return useQuery({
    queryKey: memoryKeys.userSettings,
    queryFn: memoryApi.getUserSettings,
    staleTime: 30_000,
  })
}

export function useUpdateUserMemorySettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: UserMemorySettingsUpdate) => memoryApi.updateUserSettings(data),
    onSuccess: () => invalidateMemory(qc),
  })
}

export function useAgentMemorySettings(agentId: string) {
  return useQuery({
    queryKey: memoryKeys.agentSettings(agentId),
    queryFn: () => memoryApi.getAgentSettings(agentId),
    enabled: !!agentId,
    staleTime: 30_000,
  })
}

export function useUpdateAgentMemorySettings(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: AgentMemorySettingsUpdate) => memoryApi.updateAgentSettings(agentId, data),
    onSuccess: () => invalidateMemory(qc),
  })
}

/** The approval mode in force and the tenant's allowed modes (plan 15, 5.11, Q9). */
export function useMemoryPolicy() {
  return useQuery({
    queryKey: memoryKeys.policy,
    queryFn: memoryApi.getPolicy,
    staleTime: 30_000,
  })
}

export function useMemories(params?: MemoryListParams) {
  return useQuery({
    queryKey: memoryKeys.list(params),
    queryFn: () => memoryApi.list(params),
    staleTime: 10_000,
  })
}

export function useMemoryProposal(proposalId?: string) {
  return useQuery({
    queryKey: memoryKeys.proposal(proposalId ?? ''),
    queryFn: () => memoryApi.getProposal(proposalId ?? ''),
    enabled: Boolean(proposalId),
    staleTime: 5_000,
    retry: false,
  })
}

export function useCreateMemory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: MemoryRecordCreate) => memoryApi.create(data),
    onSuccess: () => invalidateMemory(qc),
  })
}

export function useUpdateMemory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: MemoryRecordUpdate }) =>
      memoryApi.update(id, data),
    onSuccess: () => invalidateMemory(qc),
  })
}

export function useDeleteMemory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => memoryApi.delete(id),
    onSuccess: () => invalidateMemory(qc),
  })
}

export function useApproveMemoryProposal() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => memoryApi.approveProposal(id),
    onSuccess: () => invalidateMemory(qc),
  })
}

export function useRejectMemoryProposal() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => memoryApi.rejectProposal(id),
    onSuccess: () => invalidateMemory(qc),
  })
}

export function useEditAndApproveMemoryProposal() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: MemoryProposalEditApprove }) =>
      memoryApi.editAndApproveProposal(id, data),
    onSuccess: () => invalidateMemory(qc),
  })
}

export function useRevokeMemory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string | null }) =>
      memoryApi.revoke(id, reason),
    onSuccess: () => invalidateMemory(qc),
  })
}

export function useRestoreMemory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => memoryApi.restore(id),
    onSuccess: () => invalidateMemory(qc),
  })
}

export function useMemoryRevisions(memoryId: string | null) {
  return useQuery({
    queryKey: memoryKeys.revisions(memoryId ?? ''),
    queryFn: () => memoryApi.listRevisions(memoryId ?? ''),
    enabled: Boolean(memoryId),
    staleTime: 5_000,
  })
}

export function useMemoryActivity() {
  return useQuery({
    queryKey: memoryKeys.activity,
    queryFn: memoryApi.listActivity,
    staleTime: 10_000,
  })
}

export function useMemoryProposals(status: MemoryProposalStatusFilter = 'pending') {
  return useQuery({
    queryKey: memoryKeys.proposals(status),
    queryFn: () => memoryApi.listProposals(status),
    staleTime: 10_000,
  })
}

/** The count a scoped delete must confirm; always refetched so the number is current. */
export function useMemoryScopeCount(query: MemoryScopeQuery, enabled: boolean) {
  return useQuery({
    queryKey: memoryKeys.scopeCount(query),
    queryFn: () => memoryApi.countScope(query),
    enabled: enabled && (query.scope !== 'agent' || Boolean(query.agentId)),
    staleTime: 0,
    gcTime: 0,
  })
}

export function useDeleteMemoryScope() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: MemoryScopeDelete) => memoryApi.deleteScope(data),
    onSettled: () => invalidateMemory(qc),
  })
}

const EXPORT_FILENAMES: Record<MemoryExportFormat, string> = {
  json: 'memories.json',
  markdown: 'memories.md',
}

/** Downloads the export file; the export is an audited event, so activity refreshes. */
export function useExportMemories() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ format, query }: { format: MemoryExportFormat; query: MemoryScopeQuery }) =>
      downloadApiFile(memoryExportPath(format, query), EXPORT_FILENAMES[format]),
    onSuccess: () => qc.invalidateQueries({ queryKey: memoryKeys.activity }),
  })
}

/** 내 프로필, plus that agent's 작업 메모 when `agentId` is given (plan 15, 5.12). */
export function useMemoryProfiles(agentId: string | null) {
  return useQuery({
    queryKey: memoryKeys.profiles(agentId),
    queryFn: () => memoryApi.getProfiles(agentId),
    staleTime: 10_000,
  })
}

/** Profile saves and pins change both the profile documents and the records' `pinned`. */
export function useUpdateMemoryProfile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ kind, data }: { kind: MemoryProfileKind; data: MemoryProfileUpdate }) =>
      memoryApi.updateProfile(kind, data),
    onSuccess: () => invalidateMemory(qc),
    // A version conflict means the cached document is stale; fetch the newer one.
    onError: (error) => {
      if (error instanceof ApiError && error.code === 'MEMORY_PROFILE_CHANGED') {
        qc.invalidateQueries({ queryKey: memoryKeys.profilesAll })
      }
    },
  })
}

export function usePinMemory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, pinned }: { id: string; pinned: boolean }) =>
      pinned ? memoryApi.pin(id) : memoryApi.unpin(id),
    onSuccess: () => invalidateMemory(qc),
  })
}

/** A dry run of chat recall; it records nothing, so nothing is invalidated. */
export function useMemoryRecallPreview() {
  return useMutation({
    mutationFn: (data: MemoryRecallPreviewRequest) => memoryApi.previewRecall(data),
  })
}
