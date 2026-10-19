'use client'

import { useQuery } from '@tanstack/react-query'

import { tenantCatalogApi } from '@/lib/api/tenant-catalog'
import { tenantAdminKeys } from '@/lib/query-keys/tenant-admin'
import type {
  CatalogOverride,
  TenantCredentialCreate,
  TenantCredentialUpdate,
  TenantLlmSettingUpdate,
  TenantModelCreate,
  TenantModelUpdate,
} from '@/lib/types/tenant-admin'
import { useTenantMutation } from './use-tenant-admin'

/** S07 reads: company admins only (`can_manage_tenant_catalog`); no retry on 403/404. */
export function useTenantModels(tenantId: string) {
  return useQuery({
    queryKey: tenantAdminKeys.models(tenantId),
    queryFn: () => tenantCatalogApi.listModels(tenantId),
    retry: false,
  })
}

export function useTenantCredentials(tenantId: string) {
  return useQuery({
    queryKey: tenantAdminKeys.credentials(tenantId),
    queryFn: () => tenantCatalogApi.listCredentials(tenantId),
    retry: false,
  })
}

export function useTenantCatalogPolicy(tenantId: string) {
  return useQuery({
    queryKey: tenantAdminKeys.catalog(tenantId),
    queryFn: () => tenantCatalogApi.getCatalogPolicy(tenantId),
    retry: false,
  })
}

export function useTenantLlmSettings(tenantId: string) {
  return useQuery({
    queryKey: tenantAdminKeys.llm(tenantId),
    queryFn: () => tenantCatalogApi.getLlmSettings(tenantId),
    retry: false,
  })
}

export function useTenantModelMutations(tenantId: string) {
  return {
    create: useTenantMutation(tenantId, (data: TenantModelCreate) =>
      tenantCatalogApi.createModel(tenantId, data),
    ),
    update: useTenantMutation(tenantId, ({ id, data }: { id: string; data: TenantModelUpdate }) =>
      tenantCatalogApi.updateModel(tenantId, id, data),
    ),
    remove: useTenantMutation(tenantId, (id: string) => tenantCatalogApi.deleteModel(tenantId, id)),
  }
}

export function useTenantCredentialMutations(tenantId: string) {
  return {
    create: useTenantMutation(tenantId, (data: TenantCredentialCreate) =>
      tenantCatalogApi.createCredential(tenantId, data),
    ),
    update: useTenantMutation(
      tenantId,
      ({ id, data }: { id: string; data: TenantCredentialUpdate }) =>
        tenantCatalogApi.updateCredential(tenantId, id, data),
    ),
    remove: useTenantMutation(tenantId, (id: string) =>
      tenantCatalogApi.deleteCredential(tenantId, id),
    ),
  }
}

export function useUpdateTenantCatalogPolicy(tenantId: string) {
  return useTenantMutation(
    tenantId,
    ({ items, version }: { items: CatalogOverride[]; version: string }) =>
      tenantCatalogApi.putCatalogPolicy(tenantId, items, version),
  )
}

export function useUpdateTenantLlmSetting(tenantId: string) {
  return useTenantMutation(tenantId, (data: TenantLlmSettingUpdate) =>
    tenantCatalogApi.putLlmSetting(tenantId, data),
  )
}
