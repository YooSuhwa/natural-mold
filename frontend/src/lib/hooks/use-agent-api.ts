'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { agentApi } from '@/lib/api/agent-api'
import type { AgentApiKeyCreateRequest, AgentDeploymentCreateRequest } from '@/lib/types'

const keys = {
  root: ['agent-api'] as const,
  candidates: ['agent-api', 'deployment-candidates'] as const,
  deployments: ['agent-api', 'deployments'] as const,
  apiKeys: ['agent-api', 'keys'] as const,
}

export function useAgentDeploymentCandidates() {
  return useQuery({
    queryKey: keys.candidates,
    queryFn: agentApi.listDeploymentCandidates,
  })
}

export function useAgentDeployments() {
  return useQuery({ queryKey: keys.deployments, queryFn: agentApi.listDeployments })
}

export function useAgentApiKeys() {
  return useQuery({ queryKey: keys.apiKeys, queryFn: agentApi.listKeys })
}

export function useCreateAgentDeployment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: AgentDeploymentCreateRequest) => agentApi.createDeployment(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.candidates })
      qc.invalidateQueries({ queryKey: keys.deployments })
    },
  })
}

export function useUpdateAgentDeployment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string
      data: Parameters<typeof agentApi.updateDeployment>[1]
    }) => agentApi.updateDeployment(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.candidates })
      qc.invalidateQueries({ queryKey: keys.deployments })
      qc.invalidateQueries({ queryKey: keys.apiKeys })
    },
  })
}

export function useCreateAgentApiKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: AgentApiKeyCreateRequest) => agentApi.createKey(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.apiKeys }),
  })
}

export function useRevokeAgentApiKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => agentApi.revokeKey(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.apiKeys }),
  })
}
