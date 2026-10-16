'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAtom } from 'jotai'
import { meOrganizationsApi, tenantOrganizationsApi } from '@/lib/api/organizations'
import { isOrgIndependentQueryKey, organizationQueryKeys } from '@/lib/query-keys/organizations'
import { activeOrganizationIdAtom } from '@/lib/stores/organization-store'
import type {
  OrganizationSummary,
  TenantOrganizationArchive,
  TenantOrganizationCreate,
  TenantOrganizationPageQuery,
  TenantOrganizationRename,
} from '@/lib/types/organization'
import { requiredQueryValue } from './required-query-value'

export function useMyOrganizations() {
  return useQuery({
    queryKey: organizationQueryKeys.mine,
    queryFn: meOrganizationsApi.list,
    staleTime: 60_000,
  })
}

function compareText(a: string, b: string): number {
  if (a < b) return -1
  if (a > b) return 1
  return 0
}

/**
 * Mirror of the backend fallback (`resolve_org_context`) for when this browser
 * has no remembered selection: first active-company org by tenant name, org
 * name, then id.
 */
export function fallbackActiveOrganization(
  organizations: readonly OrganizationSummary[],
): OrganizationSummary | null {
  const candidates = organizations
    .filter((org) => org.tenant_status === 'active')
    .sort(
      (a, b) =>
        compareText(a.tenant_name, b.tenant_name) ||
        compareText(a.name, b.name) ||
        compareText(a.id, b.id),
    )
  return candidates[0] ?? null
}

export function resolveActiveOrganization(
  organizations: readonly OrganizationSummary[],
  rememberedId: string | null,
): OrganizationSummary | null {
  const remembered = rememberedId ? organizations.find((org) => org.id === rememberedId) : undefined
  return remembered ?? fallbackActiveOrganization(organizations)
}

export function useActiveOrganization() {
  const query = useMyOrganizations()
  const [rememberedId] = useAtom(activeOrganizationIdAtom)
  const organizations = query.data ?? []
  return {
    ...query,
    organizations,
    activeOrganization: resolveActiveOrganization(organizations, rememberedId),
  }
}

export function useSwitchActiveOrganization() {
  const qc = useQueryClient()
  const [, setRememberedId] = useAtom(activeOrganizationIdAtom)
  return useMutation({
    mutationFn: (orgId: string) => meOrganizationsApi.setActive(orgId),
    onSuccess: async (result) => {
      setRememberedId(result.org_id)
      // Reset (not just invalidate) so the previous org's data never flashes
      // while the new org's data loads.
      await qc.resetQueries({ predicate: (q) => !isOrgIndependentQueryKey(q.queryKey) })
    },
  })
}

export function useTenantOrganizations(
  tenantId: string | null | undefined,
  query: TenantOrganizationPageQuery = {},
) {
  return useQuery({
    queryKey: organizationQueryKeys.tenantList(tenantId, query),
    queryFn: () => tenantOrganizationsApi.list(requiredQueryValue(tenantId, 'tenant id'), query),
    enabled: !!tenantId,
  })
}

function useInvalidateTenantOrganizations() {
  const qc = useQueryClient()
  return (tenantId: string) =>
    Promise.all([
      qc.invalidateQueries({ queryKey: organizationQueryKeys.tenantAll(tenantId) }),
      qc.invalidateQueries({ queryKey: organizationQueryKeys.mine }),
    ])
}

export function useCreateTenantOrganization(tenantId: string) {
  const invalidate = useInvalidateTenantOrganizations()
  return useMutation({
    mutationFn: (data: TenantOrganizationCreate) => tenantOrganizationsApi.create(tenantId, data),
    onSuccess: () => invalidate(tenantId),
  })
}

export function useRenameTenantOrganization(tenantId: string) {
  const invalidate = useInvalidateTenantOrganizations()
  return useMutation({
    mutationFn: ({
      orgId,
      data,
      updatedAt,
    }: {
      orgId: string
      data: TenantOrganizationRename
      updatedAt: string
    }) => tenantOrganizationsApi.rename(tenantId, orgId, data, updatedAt),
    onSuccess: () => invalidate(tenantId),
  })
}

export function useArchiveTenantOrganization(tenantId: string) {
  const invalidate = useInvalidateTenantOrganizations()
  return useMutation({
    mutationFn: ({
      orgId,
      data,
      updatedAt,
    }: {
      orgId: string
      data: TenantOrganizationArchive
      updatedAt: string
    }) => tenantOrganizationsApi.archive(tenantId, orgId, data, updatedAt),
    onSuccess: () => invalidate(tenantId),
  })
}

export function useRestoreTenantOrganization(tenantId: string) {
  const invalidate = useInvalidateTenantOrganizations()
  return useMutation({
    mutationFn: ({ orgId, updatedAt }: { orgId: string; updatedAt: string }) =>
      tenantOrganizationsApi.restore(tenantId, orgId, updatedAt),
    onSuccess: () => invalidate(tenantId),
  })
}
