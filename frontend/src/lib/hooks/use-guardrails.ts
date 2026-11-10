'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { agentGuardrailsApi, guardrailsApi, toolPoliciesApi } from '@/lib/api/guardrails'
import { agentQueryKeys } from '@/lib/query-keys/agents'
import { tenantAdminKeys } from '@/lib/query-keys/tenant-admin'
import { toolApprovalQueryKeys } from '@/lib/query-keys/tool-approval'
import type {
  AgentGuardrailsUpdate,
  GuardrailPolicyUpdate,
  GuardrailTemplateCreate,
  GuardrailTemplateUpdate,
  ToolPolicyUpdate,
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

export function useGuardrailTemplateVersions(tenantId: string, templateId: string) {
  return useQuery({
    queryKey: tenantAdminKeys.guardrailTemplateVersions(tenantId, templateId),
    queryFn: () => guardrailsApi.listVersions(tenantId, templateId),
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
    revert: useTenantMutation(
      tenantId,
      ({ id, target, version }: TemplateVersion & { target: number }) =>
        guardrailsApi.revertTemplate(tenantId, id, target, version),
    ),
    archive: useTenantMutation(tenantId, ({ id, version }: TemplateVersion) =>
      guardrailsApi.archiveTemplate(tenantId, id, version),
    ),
  }
}

/** Company members read the per-tool policies; the catalog of targets is for admins. */
export function useToolPolicies(tenantId: string) {
  return useQuery({
    queryKey: tenantAdminKeys.toolPolicies(tenantId),
    queryFn: () => toolPoliciesApi.list(tenantId),
    retry: false,
  })
}

export function useToolPolicyTargets(tenantId: string, enabled: boolean) {
  return useQuery({
    queryKey: tenantAdminKeys.toolPolicyTargets(tenantId),
    queryFn: () => toolPoliciesApi.targets(tenantId),
    enabled,
    retry: false,
  })
}

interface ToolPolicyInput {
  key: string
  data: ToolPolicyUpdate
}

export function useSaveToolPolicy(tenantId: string) {
  const queryClient = useQueryClient()
  return useTenantMutation(tenantId, async ({ key, data }: ToolPolicyInput) => {
    const saved = await toolPoliciesApi.save(tenantId, key, data)
    // Every agent's "what applies" table reads the company's per-tool policy.
    void queryClient.invalidateQueries({ queryKey: toolApprovalQueryKeys.all })
    return saved
  })
}

/** A preview: it changes nothing, so no query is refreshed. */
export function useToolPolicyImpact(tenantId: string) {
  return useMutation({
    mutationFn: ({ key, data }: ToolPolicyInput) => toolPoliciesApi.impact(tenantId, key, data),
  })
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
