'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { agentBlueprintApi } from '@/lib/api/marketplace'
import { agentQueryKeys } from '@/lib/query-keys/agents'
import { agentBlueprintQueryKeys } from '@/lib/query-keys/agent-blueprints'
import type { CreateAgentFromBlueprintBody } from '@/lib/types/marketplace'

import { requireQueryId } from './query-id'

export function useAgentBlueprints(enabled = true) {
  return useQuery({
    queryKey: agentBlueprintQueryKeys.all,
    queryFn: () => agentBlueprintApi.list(),
    enabled,
    staleTime: 30_000,
  })
}

export function useAgentBlueprint(blueprintId: string | null | undefined) {
  return useQuery({
    queryKey: agentBlueprintQueryKeys.detail(blueprintId),
    queryFn: () => agentBlueprintApi.get(requireQueryId(blueprintId, 'blueprintId')),
    enabled: !!blueprintId,
  })
}

export function useCreateAgentFromBlueprint() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: ({
      blueprintId,
      body,
    }: {
      readonly blueprintId: string
      readonly body: CreateAgentFromBlueprintBody
    }) => agentBlueprintApi.createAgent(blueprintId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: agentBlueprintQueryKeys.all })
      qc.invalidateQueries({ queryKey: agentQueryKeys.all })
    },
  })
}
