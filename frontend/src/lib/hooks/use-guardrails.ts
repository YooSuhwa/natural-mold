'use client'

import { useQuery } from '@tanstack/react-query'

import { guardrailsApi } from '@/lib/api/guardrails'
import { tenantAdminKeys } from '@/lib/query-keys/tenant-admin'
import type {
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

export function useGuardrailTemplateMutations(tenantId: string) {
  return {
    create: useTenantMutation(tenantId, (data: GuardrailTemplateCreate) =>
      guardrailsApi.createTemplate(tenantId, data),
    ),
    update: useTenantMutation(
      tenantId,
      ({ id, data }: { id: string; data: GuardrailTemplateUpdate }) =>
        guardrailsApi.updateTemplate(tenantId, id, data),
    ),
    publish: useTenantMutation(tenantId, (id: string) =>
      guardrailsApi.publishTemplate(tenantId, id),
    ),
    archive: useTenantMutation(tenantId, (id: string) =>
      guardrailsApi.archiveTemplate(tenantId, id),
    ),
  }
}
