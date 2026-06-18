'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { triggersApi } from '@/lib/api/triggers'
import { agentQueryKeys } from '@/lib/query-keys/agents'
import type { TriggerCreateRequest, TriggerUpdateRequest } from '@/lib/types'

export const triggerKeys = {
  all: ['triggers'] as const,
  summary: ['triggers', 'summary'] as const,
  agent: (agentId: string) => ['triggers', agentId] as const,
  runs: (triggerId: string) => ['triggers', triggerId, 'runs'] as const,
}

function invalidateTriggerQueries(qc: ReturnType<typeof useQueryClient>, agentId?: string) {
  qc.invalidateQueries({ queryKey: triggerKeys.all })
  qc.invalidateQueries({ queryKey: triggerKeys.summary })
  if (agentId) qc.invalidateQueries({ queryKey: triggerKeys.agent(agentId) })
  qc.invalidateQueries({ queryKey: agentQueryKeys.all })
}

export function useAllTriggers() {
  return useQuery({
    queryKey: triggerKeys.all,
    queryFn: triggersApi.listAll,
    staleTime: 30000,
  })
}

export function useTriggerSummary() {
  return useQuery({
    queryKey: triggerKeys.summary,
    queryFn: triggersApi.summary,
    staleTime: 30000,
  })
}

export function useTriggers(agentId: string) {
  return useQuery({
    queryKey: triggerKeys.agent(agentId),
    queryFn: () => triggersApi.list(agentId),
    staleTime: 30000,
  })
}

export function useCreateTrigger(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: TriggerCreateRequest) => triggersApi.create(agentId, data),
    onSuccess: () => invalidateTriggerQueries(qc, agentId),
  })
}

export function useUpdateTrigger(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ triggerId, data }: { triggerId: string; data: TriggerUpdateRequest }) =>
      triggersApi.update(agentId, triggerId, data),
    onSuccess: () => invalidateTriggerQueries(qc, agentId),
  })
}

export function useDeleteTrigger(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (triggerId: string) => triggersApi.delete(agentId, triggerId),
    onSuccess: () => invalidateTriggerQueries(qc, agentId),
  })
}

export function useUpdateTriggerGlobal() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ triggerId, data }: { triggerId: string; data: TriggerUpdateRequest }) =>
      triggersApi.updateGlobal(triggerId, data),
    onSuccess: () => invalidateTriggerQueries(qc),
  })
}

export function useDeleteTriggerGlobal() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (triggerId: string) => triggersApi.deleteGlobal(triggerId),
    onSuccess: () => invalidateTriggerQueries(qc),
  })
}

export function useRunTriggerNow() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (triggerId: string) => triggersApi.runNow(triggerId),
    onSuccess: (_run, triggerId) => {
      invalidateTriggerQueries(qc)
      qc.invalidateQueries({ queryKey: triggerKeys.runs(triggerId) })
    },
  })
}

export function useTriggerRuns(triggerId: string | null) {
  return useQuery({
    queryKey: triggerKeys.runs(triggerId ?? ''),
    queryFn: () => triggersApi.runs(triggerId ?? ''),
    enabled: !!triggerId,
  })
}
