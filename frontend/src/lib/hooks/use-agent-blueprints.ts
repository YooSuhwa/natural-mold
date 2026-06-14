'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { agentBlueprintApi } from '@/lib/api/marketplace'
import type { CreateAgentFromBlueprintBody } from '@/lib/types/marketplace'

import { requireQueryId } from './query-id'

const AGENT_BLUEPRINTS_KEY = ['agent-blueprints'] as const

export function useAgentBlueprints(enabled = true) {
  return useQuery({
    queryKey: AGENT_BLUEPRINTS_KEY,
    queryFn: () => agentBlueprintApi.list(),
    enabled,
    staleTime: 30_000,
  })
}

export function useAgentBlueprint(blueprintId: string | null | undefined) {
  return useQuery({
    queryKey: ['agent-blueprints', blueprintId],
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
      qc.invalidateQueries({ queryKey: AGENT_BLUEPRINTS_KEY })
      qc.invalidateQueries({ queryKey: ['agents'] })
    },
  })
}
