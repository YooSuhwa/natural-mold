'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { agentsApi } from '@/lib/api/agents'
import { agentQueryKeys } from '@/lib/query-keys/agents'
import { skillQueryKeys } from '@/lib/query-keys/skills'
import type { AgentCreateRequest, AgentUpdateRequest } from '@/lib/types'

export function useAgents() {
  return useQuery({ queryKey: agentQueryKeys.all, queryFn: agentsApi.list })
}

export function useAgentSummaries() {
  return useQuery({ queryKey: agentQueryKeys.summary, queryFn: agentsApi.summary })
}

export function useAgent(id: string) {
  return useQuery({
    queryKey: agentQueryKeys.detail(id),
    queryFn: () => agentsApi.get(id),
    enabled: !!id,
  })
}

export function useCreateAgent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: AgentCreateRequest) => agentsApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: agentQueryKeys.all })
      // skill_ids 저장은 스킬 목록의 연결 카운트(역집계)를 바꾼다 (Phase 2 §2.3).
      qc.invalidateQueries({ queryKey: skillQueryKeys.all })
    },
  })
}

export function useUpdateAgent(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: AgentUpdateRequest) => agentsApi.update(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: agentQueryKeys.all })
      qc.invalidateQueries({ queryKey: agentQueryKeys.detail(id) })
      qc.invalidateQueries({ queryKey: skillQueryKeys.all })
    },
  })
}

export function useDeleteAgent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => agentsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: agentQueryKeys.all })
      // skill_ids 저장은 스킬 목록의 연결 카운트(역집계)를 바꾼다 (Phase 2 §2.3).
      qc.invalidateQueries({ queryKey: skillQueryKeys.all })
    },
  })
}

export function useToggleFavorite() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => agentsApi.toggleFavorite(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: agentQueryKeys.all }),
  })
}

export function useGenerateAgentImage(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => agentsApi.generateImage(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: agentQueryKeys.all })
      qc.invalidateQueries({ queryKey: agentQueryKeys.detail(id) })
    },
  })
}
