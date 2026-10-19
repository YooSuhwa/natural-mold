'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { platformTenantsApi } from '@/lib/api/platform-tenants'
import { downloadApiFile } from '@/lib/hooks/use-api-object-url'
import { platformTenantQueryKeys as keys } from '@/lib/query-keys/platform-tenants'
import type {
  PlatformTenant,
  PlatformTenantCreate,
  PlatformTenantExport,
  PlatformTenantPatch,
  PlatformTenantStatus,
} from '@/lib/types/platform-tenant'

/** Queued/running export jobs refresh quickly so the progress row moves (S04). */
export const EXPORT_POLL_MS = 3_000

/**
 * The operator capability (`platform_tenant_management_enabled`: super user in SaaS).
 * Fail closed: loading, errors and a missing flag all mean "not an operator".
 */
export function usePlatformOperatorAccess(): { pending: boolean; allowed: boolean } {
  const query = useQuery({
    queryKey: keys.installation(),
    queryFn: platformTenantsApi.installation,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  return {
    pending: query.isPending,
    allowed: query.data?.platform_tenant_management_enabled === true,
  }
}

export function usePlatformTenants(
  status: PlatformTenantStatus | null,
  search: string,
  cursor: string | null,
) {
  return useQuery({
    queryKey: keys.list(status, search, cursor),
    queryFn: () => platformTenantsApi.list({ status, search, cursor }),
  })
}

export function usePlatformTenant(id: string) {
  return useQuery({ queryKey: keys.detail(id), queryFn: () => platformTenantsApi.get(id) })
}

export function usePlatformTenantUsage(id: string) {
  return useQuery({ queryKey: keys.usage(id), queryFn: () => platformTenantsApi.usage(id) })
}

export function usePlatformTenantDeletion(id: string) {
  return useQuery({ queryKey: keys.deletion(id), queryFn: () => platformTenantsApi.deletion(id) })
}

function hasLiveExport(items: PlatformTenantExport[] | undefined): boolean {
  return !!items?.some((job) => job.status === 'queued' || job.status === 'running')
}

export function usePlatformTenantExports(id: string) {
  return useQuery({
    queryKey: keys.exports(id),
    queryFn: () => platformTenantsApi.exports(id),
    refetchInterval: (query) => (hasLiveExport(query.state.data?.items) ? EXPORT_POLL_MS : false),
  })
}

function useRefresh() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: keys.all })
}

export function useCreatePlatformTenant() {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (data: PlatformTenantCreate) => platformTenantsApi.create(data),
    onSuccess: refresh,
  })
}

export function useUpdatePlatformTenant(tenant: PlatformTenant) {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (data: PlatformTenantPatch) =>
      platformTenantsApi.update(tenant.id, data, tenant.updated_at),
    onSuccess: refresh,
  })
}

export type PlatformTenantAction = 'suspend' | 'resume' | 'cancelDeletion'

/** Suspend, resume or cancel a deletion schedule, with the tenant's current version. */
export function usePlatformTenantTransition() {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: async ({
      tenant,
      action,
    }: {
      tenant: PlatformTenant
      action: PlatformTenantAction
    }): Promise<void> => {
      await platformTenantsApi[action](tenant.id, tenant.updated_at)
    },
    onSettled: refresh,
  })
}

export function useSchedulePlatformTenantDeletion() {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: ({ tenant, slug }: { tenant: PlatformTenant; slug: string }) =>
      platformTenantsApi.scheduleDeletion(tenant.id, slug, tenant.updated_at),
    onSettled: refresh,
  })
}

export function useStartPlatformTenantExport() {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (id: string) => platformTenantsApi.startExport(id),
    onSettled: refresh,
  })
}

export function downloadPlatformTenantExport(tenantId: string, exportId: string): Promise<void> {
  return downloadApiFile(
    platformTenantsApi.exportDownloadPath(tenantId, exportId),
    `tenant-${tenantId}-export-${exportId}.zip`,
  )
}
