"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { triggersApi } from "@/lib/api/triggers"
import type { TriggerCreateRequest, TriggerUpdateRequest } from "@/lib/types"

export function useTriggers(agentId: string) {
  return useQuery({
    queryKey: ["triggers", agentId],
    queryFn: () => triggersApi.list(agentId),
    staleTime: 30000,
  })
}

export function useCreateTrigger(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: TriggerCreateRequest) => triggersApi.create(agentId, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["triggers", agentId] }),
  })
}

export function useUpdateTrigger(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ triggerId, data }: { triggerId: string; data: TriggerUpdateRequest }) =>
      triggersApi.update(agentId, triggerId, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["triggers", agentId] }),
  })
}

export function useDeleteTrigger(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (triggerId: string) => triggersApi.delete(agentId, triggerId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["triggers", agentId] }),
  })
}
