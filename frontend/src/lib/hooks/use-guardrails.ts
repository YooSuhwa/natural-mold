'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { agentGuardrailsApi, guardrailsApi } from '@/lib/api/guardrails'
import { agentQueryKeys } from '@/lib/query-keys/agents'
import { tenantAdminKeys } from '@/lib/query-keys/tenant-admin'
import type {
  AgentGuardrailsUpdate,
  GuardrailPolicyUpdate,
  GuardrailTemplateCreate,
  GuardrailTemplateUpdate,
} from '@/lib/types/guardrails'
import { useTenantMutation } from './use-tenant-admin'

/** Company members read the policy; template edits are for company admins. */
export function useGuardrailPolicy(tenantId: string) {
  return useQuery({
    queryKey: tenantAdminKeys.guardrails(tenantId),
    queryFn: () => guardrailsApi.getPolicy(tenantId),
    retry: false,
  })
}

export function useGuardrailTemplates(tenantId: string, enabled: boolean) {
  return useQuery({
    queryKey: tenantAdminKeys.guardrailTemplates(tenantId),
    queryFn: () => guardrailsApi.listTemplates(tenantId),
    enabled,
    retry: false,
  })
}

export function useUpdateGuardrailPolicy(tenantId: string) {
  return useTenantMutation(
    tenantId,
    ({ data, version }: { data: GuardrailPolicyUpdate; version: string }) =>
      guardrailsApi.putPolicy(tenantId, data, version),
  )
}

interface TemplateVersion {
  id: string
  version: string
}

export function useGuardrailTemplateMutations(tenantId: string) {
  return {
    create: useTenantMutation(tenantId, (data: GuardrailTemplateCreate) =>
      guardrailsApi.createTemplate(tenantId, data),
    ),
    update: useTenantMutation(
      tenantId,
      ({ id, data, version }: { id: string; data: GuardrailTemplateUpdate; version: string }) =>
        guardrailsApi.updateTemplate(tenantId, id, data, version),
    ),
    publish: useTenantMutation(tenantId, ({ id, version }: TemplateVersion) =>
      guardrailsApi.publishTemplate(tenantId, id, version),
    ),
    archive: useTenantMutation(tenantId, ({ id, version }: TemplateVersion) =>
      guardrailsApi.archiveTemplate(tenantId, id, version),
    ),
  }
}

export function useAgentGuardrails(agentId: string) {
  return useQuery({
    queryKey: agentQueryKeys.guardrails(agentId),
    queryFn: () => agentGuardrailsApi.get(agentId),
    retry: false,
  })
}

export function useUpdateAgentGuardrails(agentId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: AgentGuardrailsUpdate) => agentGuardrailsApi.put(agentId, data),
    onSuccess: (result) => queryClient.setQueryData(agentQueryKeys.guardrails(agentId), result),
  })
}
