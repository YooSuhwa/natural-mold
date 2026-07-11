'use client'

import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { agentsApi } from '@/lib/api/agents'
import { agentQueryKeys } from '@/lib/query-keys/agents'
import type { AgentCreateRequest, AgentUpdateRequest } from '@/lib/types'

// skill_ids 저장은 스킬 목록/상세의 연결 카운트(역집계)를 바꾼다 (Phase 2 §2.3).
// used_by_count는 목록(['skills', params])·상세(['skills', id]) — 길이 2 키 —
// 에만 실린다. skillQueryKeys.all(['skills'] prefix)로 쓸면 staleTime: Infinity
// 계약인 리비전 스냅샷·평가 서브트리(길이 3+)까지 에이전트 저장마다 무효화돼
// 불변 스냅샷 캐시가 매 방문 refetch 레이어로 격하된다 (R5).
function invalidateSkillLinkCounts(qc: QueryClient) {
  void qc.invalidateQueries({
    predicate: (query) => query.queryKey[0] === 'skills' && query.queryKey.length === 2,
  })
}

export function useAgents(options?: { readonly enabled?: boolean }) {
  return useQuery({
    queryKey: agentQueryKeys.all,
    queryFn: agentsApi.list,
    enabled: options?.enabled ?? true,
  })
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
      invalidateSkillLinkCounts(qc)
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
      invalidateSkillLinkCounts(qc)
    },
  })
}

export function useDeleteAgent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => agentsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: agentQueryKeys.all })
      invalidateSkillLinkCounts(qc)
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
