'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { agentsApi } from '@/lib/api/agents'
import type { AgentCreateRequest, AgentUpdateRequest } from '@/lib/types'

export function useAgents() {
  return useQuery({ queryKey: ['agents'], queryFn: agentsApi.list })
}

export function useAgent(id: string) {
  return useQuery({ queryKey: ['agents', id], queryFn: () => agentsApi.get(id), enabled: !!id })
}

export function useCreateAgent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: AgentCreateRequest) => agentsApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents'] }),
  })
}

export function useUpdateAgent(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: AgentUpdateRequest) => agentsApi.update(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agents'] })
      qc.invalidateQueries({ queryKey: ['agents', id] })
    },
  })
}

export function useDeleteAgent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => agentsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents'] }),
  })
}
