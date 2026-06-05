'use client'

import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'

import { memoryApi } from '@/lib/api/memory'
import type {
  AgentMemorySettingsUpdate,
  MemoryProposalEditApprove,
  MemoryRecordCreate,
  MemoryRecordUpdate,
  MemoryScopeFilter,
  UserMemorySettingsUpdate,
} from '@/lib/types/memory'

export const memoryKeys = {
  all: ['memory'] as const,
  settings: ['memory', 'settings'] as const,
  userSettings: ['memory', 'settings', 'user'] as const,
  agentSettings: (agentId: string) => ['memory', 'settings', 'agent', agentId] as const,
  proposal: (proposalId: string) => ['memory', 'proposal', proposalId] as const,
  list: (params?: { scope?: MemoryScopeFilter; agentId?: string | null; q?: string | null }) =>
    ['memory', 'records', params ?? {}] as const,
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

export function useMemories(params?: {
  scope?: MemoryScopeFilter
  agentId?: string | null
  q?: string | null
}) {
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
