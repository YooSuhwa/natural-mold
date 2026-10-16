'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '@/lib/api/errors'
import {
  meOrganizationsApi,
  orgAdminApi,
  tenantOrganizationsApi,
  tenantsApi,
} from '@/lib/api/organizations'
import { isOrgIndependentQueryKey, organizationQueryKeys } from '@/lib/query-keys/organizations'
import type {
  CapabilityAssignment,
  CapabilityGrantView,
  DirectoryPage,
  DirectoryQuery,
  GroupCreate,
  GroupPatch,
  MemberStatus,
  OrganizationPatch,
  OrganizationSummary,
  Principal,
  PrincipalSearchType,
  RoleGrantView,
  RoleAssignment,
  TenantOrganizationArchive,
  TenantOrganizationCreate,
  TenantOrganizationPageQuery,
  TenantOrganizationRename,
} from '@/lib/types/organization'
import { requiredQueryValue } from './required-query-value'

export function isStaleVersionError(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'stale_version'
}

// ----------------------------- self navigation -----------------------------

export function useMyOrganizations() {
  return useQuery({
    queryKey: organizationQueryKeys.mine,
    queryFn: meOrganizationsApi.list,
    staleTime: 60_000,
  })
}

export function useActiveOrganization() {
  const query = useMyOrganizations()
  const organizations = query.data ?? []
  return {
    ...query,
    organizations,
    activeOrganization: organizations.find((org) => org.is_active) ?? null,
  }
}

export function useSwitchActiveOrganization() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (orgId: string) => meOrganizationsApi.setActive(orgId),
    onSuccess: async (result) => {
      qc.setQueryData<OrganizationSummary[]>(organizationQueryKeys.mine, (current) =>
        current?.map((org) => ({ ...org, is_active: org.id === result.org_id })),
      )
      // Reset (not just invalidate) so the previous org's data never flashes
      // while the new org's data loads.
      await qc.resetQueries({ predicate: (q) => !isOrgIndependentQueryKey(q.queryKey) })
      await qc.invalidateQueries({ queryKey: organizationQueryKeys.mine })
    },
  })
}

// ----------------------------- company directory -----------------------------

export function useTenantOverview(tenantId: string | null | undefined) {
  return useQuery({
    queryKey: organizationQueryKeys.tenantOverview(tenantId),
    queryFn: () => tenantsApi.overview(requiredQueryValue(tenantId, 'tenant id')),
    enabled: !!tenantId,
    retry: false,
    staleTime: 60_000,
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
    retry: false,
  })
}

function useInvalidateTenantOrganizations() {
  const qc = useQueryClient()
  return (tenantId: string) =>
    Promise.all([
      qc.invalidateQueries({ queryKey: organizationQueryKeys.tenantAll(tenantId) }),
      qc.invalidateQueries({ queryKey: organizationQueryKeys.mine }),
      qc.invalidateQueries({ queryKey: organizationQueryKeys.orgAdminRoot }),
    ])
}

export function useCreateTenantOrganization(tenantId: string) {
  const invalidate = useInvalidateTenantOrganizations()
  return useMutation({
    mutationFn: (data: TenantOrganizationCreate) => tenantOrganizationsApi.create(tenantId, data),
    onSettled: () => invalidate(tenantId),
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
    onSettled: () => invalidate(tenantId),
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
    onSettled: () => invalidate(tenantId),
  })
}

export function useRestoreTenantOrganization(tenantId: string) {
  const invalidate = useInvalidateTenantOrganizations()
  return useMutation({
    mutationFn: ({ orgId, updatedAt }: { orgId: string; updatedAt: string }) =>
      tenantOrganizationsApi.restore(tenantId, orgId, updatedAt),
    onSettled: () => invalidate(tenantId),
  })
}

// ----------------------------- organization administration -----------------------------

export function useOrganizationInfo(orgId: string | null | undefined) {
  return useQuery({
    queryKey: organizationQueryKeys.org(orgId),
    queryFn: () => orgAdminApi.get(requiredQueryValue(orgId, 'org id')),
    enabled: !!orgId,
    retry: false,
  })
}

export function useOrgMembers(orgId: string | null | undefined, query: DirectoryQuery = {}) {
  return useQuery({
    queryKey: organizationQueryKeys.directory(orgId, 'members', query),
    queryFn: () => orgAdminApi.listMembers(requiredQueryValue(orgId, 'org id'), query),
    enabled: !!orgId,
    retry: false,
  })
}

export function useOrgGroups(orgId: string | null | undefined, query: DirectoryQuery = {}) {
  return useQuery({
    queryKey: organizationQueryKeys.directory(orgId, 'groups', query),
    queryFn: () => orgAdminApi.listGroups(requiredQueryValue(orgId, 'org id'), query),
    enabled: !!orgId,
    retry: false,
  })
}

export function useOrgGroupMembers(
  orgId: string | null | undefined,
  groupId: string | null | undefined,
) {
  return useQuery({
    queryKey: organizationQueryKeys.groupMembers(orgId, groupId),
    queryFn: () =>
      orgAdminApi.listGroupMembers(
        requiredQueryValue(orgId, 'org id'),
        requiredQueryValue(groupId, 'group id'),
      ),
    enabled: !!orgId && !!groupId,
    retry: false,
  })
}

export function useOrgRoles(orgId: string | null | undefined, query: DirectoryQuery = {}) {
  return useQuery({
    queryKey: organizationQueryKeys.directory(orgId, 'roles', query),
    queryFn: () => orgAdminApi.listRoles(requiredQueryValue(orgId, 'org id'), query),
    enabled: !!orgId,
    retry: false,
  })
}

export function useOrgCapabilities(orgId: string | null | undefined, query: DirectoryQuery = {}) {
  return useQuery({
    queryKey: organizationQueryKeys.directory(orgId, 'capabilities', query),
    queryFn: () => orgAdminApi.listCapabilities(requiredQueryValue(orgId, 'org id'), query),
    enabled: !!orgId,
    retry: false,
  })
}

const GRANT_PAGE_LIMIT = 200

/** Walk every page so callers never see a partial grant set. */
async function collectAllPages<Entry>(
  load: (cursor: string | null) => Promise<DirectoryPage<Entry>>,
): Promise<DirectoryPage<Entry>> {
  const items: Entry[] = []
  let cursor: string | null = null
  let page: DirectoryPage<Entry>
  do {
    page = await load(cursor)
    items.push(...page.items)
    cursor = page.next_cursor
  } while (page.has_more && cursor)
  return { items, next_cursor: null, has_more: false, updated_at: page.updated_at }
}

type GrantKind = 'roles' | 'capabilities'
type GrantEntry<Kind extends GrantKind> = Kind extends 'roles' ? RoleGrantView : CapabilityGrantView

/**
 * One principal's *complete* role or capability set (server-filtered, all pages).
 * Replace writes overwrite the whole set, so editors must seed from this.
 */
export function usePrincipalGrantSet<Kind extends GrantKind>(
  orgId: string,
  kind: Kind,
  principal: Principal,
) {
  return useQuery({
    queryKey: organizationQueryKeys.principalGrants(orgId, kind, principal.type, principal.id),
    queryFn: () =>
      collectAllPages<GrantEntry<Kind>>((cursor) => {
        const query = { principal, cursor, limit: GRANT_PAGE_LIMIT }
        const request =
          kind === 'roles'
            ? orgAdminApi.listRoles(orgId, query)
            : orgAdminApi.listCapabilities(orgId, query)
        return request as Promise<DirectoryPage<GrantEntry<Kind>>>
      }),
    retry: false,
  })
}

export function usePrincipalSearch(
  orgId: string | null | undefined,
  q: string,
  types: readonly PrincipalSearchType[],
) {
  const trimmed = q.trim()
  return useQuery({
    queryKey: organizationQueryKeys.principals(orgId, trimmed, types.join(',')),
    queryFn: () =>
      orgAdminApi.searchPrincipals(requiredQueryValue(orgId, 'org id'), trimmed, types),
    enabled: !!orgId && trimmed.length >= 2,
    retry: false,
    staleTime: 30_000,
  })
}

/**
 * Every org write bumps the organization version, so on success *and* on
 * failure (stale_version) the whole org-admin subtree is refetched to pick up
 * the current version.
 */
function useOrgWrite<Variables, Result>(
  orgId: string,
  mutationFn: (variables: Variables & { version: string }) => Promise<Result>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn,
    onSettled: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: organizationQueryKeys.orgAll(orgId) }),
        qc.invalidateQueries({ queryKey: organizationQueryKeys.mine }),
      ]),
  })
}

export function useUpdateOrganization(orgId: string) {
  return useOrgWrite(orgId, ({ data, version }: { data: OrganizationPatch; version: string }) =>
    orgAdminApi.update(orgId, data, version),
  )
}

export function useAddOrgMember(orgId: string) {
  return useOrgWrite(orgId, ({ userId, version }: { userId: string; version: string }) =>
    orgAdminApi.addMember(orgId, userId, version),
  )
}

export function useSetOrgMemberStatus(orgId: string) {
  return useOrgWrite(
    orgId,
    ({
      userId,
      status,
      version,
    }: {
      userId: string
      status: Exclude<MemberStatus, 'removed'>
      version: string
    }) => orgAdminApi.setMemberStatus(orgId, userId, status, version),
  )
}

export function useRemoveOrgMember(orgId: string) {
  return useOrgWrite(orgId, ({ userId, version }: { userId: string; version: string }) =>
    orgAdminApi.removeMember(orgId, userId, version),
  )
}

export function useCreateOrgGroup(orgId: string) {
  return useOrgWrite(orgId, ({ data, version }: { data: GroupCreate; version: string }) =>
    orgAdminApi.createGroup(orgId, data, version),
  )
}

export function useUpdateOrgGroup(orgId: string) {
  return useOrgWrite(
    orgId,
    ({ groupId, data, version }: { groupId: string; data: GroupPatch; version: string }) =>
      orgAdminApi.updateGroup(orgId, groupId, data, version),
  )
}

export function useDeleteOrgGroup(orgId: string) {
  return useOrgWrite(orgId, ({ groupId, version }: { groupId: string; version: string }) =>
    orgAdminApi.deleteGroup(orgId, groupId, version),
  )
}

export function useAddOrgGroupMember(orgId: string) {
  return useOrgWrite(
    orgId,
    ({ groupId, userId, version }: { groupId: string; userId: string; version: string }) =>
      orgAdminApi.addGroupMember(orgId, groupId, userId, version),
  )
}

export function useRemoveOrgGroupMember(orgId: string) {
  return useOrgWrite(
    orgId,
    ({ groupId, userId, version }: { groupId: string; userId: string; version: string }) =>
      orgAdminApi.removeGroupMember(orgId, groupId, userId, version),
  )
}

export function useReplaceOrgRoles(orgId: string) {
  return useOrgWrite(orgId, ({ data, version }: { data: RoleAssignment; version: string }) =>
    orgAdminApi.replaceRoles(orgId, data, version),
  )
}

export function useReplaceOrgCapabilities(orgId: string) {
  return useOrgWrite(orgId, ({ data, version }: { data: CapabilityAssignment; version: string }) =>
    orgAdminApi.replaceCapabilities(orgId, data, version),
  )
}
