'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { credentialBindingsApi } from '@/lib/api/credential-bindings'
import { readinessApi } from '@/lib/api/readiness'
import { readinessSummaryBatcher } from '@/lib/api/readiness-summary-batcher'
import { credentialQueryKeys } from '@/lib/query-keys/credentials'
import { credentialBindingQueryKeys, readinessQueryKeys } from '@/lib/query-keys/readiness'
import { mcpServerQueryKeys } from '@/lib/query-keys/mcp-servers'
import { toolQueryKeys } from '@/lib/query-keys/tools'
import type { BindableResourceType, CredentialMode } from '@/lib/types/readiness'
import { requiredQueryValue } from './required-query-value'

/** The server caches summaries for 60 s per caller and agent (plan 12.3). */
const SUMMARY_STALE_MS = 60_000

/** S21 point check for one agent (never cached on the server). */
export function useAgentReadiness(agentId: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: readinessQueryKeys.detail(agentId ?? ''),
    queryFn: () => readinessApi.get(requiredQueryValue(agentId, 'agent id')),
    enabled: !!agentId && enabled,
    retry: false,
  })
}

/** Card badge: batched with every other card rendered in the same tick (S20). */
export function useReadinessSummary(agentId: string) {
  return useQuery({
    queryKey: readinessQueryKeys.summary(agentId),
    queryFn: () => readinessSummaryBatcher.load(agentId),
    staleTime: SUMMARY_STALE_MS,
    retry: false,
  })
}

export function useNotifyOwner(agentId: string) {
  return useMutation({ mutationFn: () => readinessApi.notifyOwner(agentId) })
}

export interface BindCredentialInput {
  resourceType: BindableResourceType
  resourceId: string
  credentialId: string
}

/** S23: connect my credential to a per-user tool or MCP server, then re-check readiness. */
export function useBindCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ resourceType, resourceId, credentialId }: BindCredentialInput) =>
      credentialBindingsApi.bind(resourceType, resourceId, credentialId),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: credentialBindingQueryKeys.all })
      await qc.invalidateQueries({ queryKey: credentialQueryKeys.all })
      await qc.invalidateQueries({ queryKey: readinessQueryKeys.all })
    },
  })
}

export interface SetCredentialModeInput {
  resourceType: BindableResourceType
  resourceId: string
  mode: CredentialMode
  definitionKey?: string | null
}

/** F-04: switch a tool or MCP server between its own account and per-user connections. */
export function useSetCredentialMode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ resourceType, resourceId, mode, definitionKey }: SetCredentialModeInput) =>
      credentialBindingsApi.setMode(resourceType, resourceId, mode, definitionKey),
    onSuccess: async (_result, { resourceType }) => {
      await qc.invalidateQueries({
        queryKey: resourceType === 'tool' ? toolQueryKeys.all : mcpServerQueryKeys.all,
      })
      await qc.invalidateQueries({ queryKey: readinessQueryKeys.all })
    },
  })
}
