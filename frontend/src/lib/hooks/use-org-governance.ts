'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { orgGovernanceApi } from '@/lib/api/org-governance'
import { organizationQueryKeys } from '@/lib/query-keys/organizations'
import type { DirectoryQuery } from '@/lib/types/organization'
import type {
  CatalogKind,
  CatalogOverride,
  InvitationCreate,
  OrgRetentionUpdate,
} from '@/lib/types/org-governance'
import { requiredQueryValue } from './required-query-value'

// Keys live under `org-admin/<orgId>` so an org switch purges them and every
// org write (which bumps the organization version) refetches them.
const keys = {
  retention: (orgId: string | null | undefined) =>
    [...organizationQueryKeys.orgAll(orgId), 'retention'] as const,
  limits: (orgId: string | null | undefined) =>
    [...organizationQueryKeys.orgAll(orgId), 'limits'] as const,
  userLimit: (orgId: string | null | undefined, userId: string | null | undefined) =>
    [...organizationQueryKeys.orgAll(orgId), 'user-limit', userId] as const,
  catalog: (orgId: string | null | undefined) =>
    [...organizationQueryKeys.orgAll(orgId), 'catalog'] as const,
  impact: (orgId: string | null | undefined, type: CatalogKind, id: string | null) =>
    [...organizationQueryKeys.orgAll(orgId), 'catalog-impact', type, id] as const,
  invitations: (orgId: string | null | undefined, query: DirectoryQuery) =>
    [...organizationQueryKeys.orgAll(orgId), 'invitations', query.cursor ?? null] as const,
}

function useOrgQuery<Result>(
  queryKey: readonly unknown[],
  orgId: string | null | undefined,
  queryFn: (orgId: string) => Promise<Result>,
  enabled = true,
) {
  return useQuery({
    queryKey,
    queryFn: () => queryFn(requiredQueryValue(orgId, 'org id')),
    enabled: !!orgId && enabled,
    retry: false,
  })
}

export function useOrgRetention(orgId: string | null | undefined) {
  return useOrgQuery(keys.retention(orgId), orgId, orgGovernanceApi.getRetention)
}

export function useOrgLimits(orgId: string | null | undefined, enabled = true) {
  return useOrgQuery(keys.limits(orgId), orgId, orgGovernanceApi.getLimits, enabled)
}

export function useOrgUserLimit(orgId: string | null | undefined, userId: string | null) {
  return useOrgQuery(
    keys.userLimit(orgId, userId),
    orgId,
    (id) => orgGovernanceApi.getUserLimit(id, requiredQueryValue(userId, 'user id')),
    !!userId,
  )
}

export function useOrgCatalog(orgId: string | null | undefined, enabled = true) {
  return useOrgQuery(keys.catalog(orgId), orgId, orgGovernanceApi.getCatalog, enabled)
}

export function useCatalogImpact(
  orgId: string | null | undefined,
  target: { type: CatalogKind; id: string } | null,
) {
  return useQuery({
    queryKey: keys.impact(orgId, target?.type ?? 'model', target?.id ?? null),
    queryFn: () => {
      if (!target) throw new Error('catalog item is required before running the query')
      return orgGovernanceApi.catalogImpact(
        requiredQueryValue(orgId, 'org id'),
        target.type,
        target.id,
      )
    },
    enabled: !!orgId && !!target,
    retry: false,
  })
}

export function useOrgInvitations(orgId: string | null | undefined, query: DirectoryQuery = {}) {
  return useOrgQuery(keys.invitations(orgId, query), orgId, (id) =>
    orgGovernanceApi.listInvitations(id, query),
  )
}

/** Every governance write bumps the organization version: refetch the org subtree. */
function useGovernanceWrite<Variables, Result>(
  orgId: string,
  mutationFn: (variables: Variables) => Promise<Result>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn,
    onSettled: () => qc.invalidateQueries({ queryKey: organizationQueryKeys.orgAll(orgId) }),
  })
}

export function useSaveOrgRetention(orgId: string) {
  return useGovernanceWrite(
    orgId,
    ({ data, version }: { data: OrgRetentionUpdate; version: string }) =>
      orgGovernanceApi.putRetention(orgId, data, version),
  )
}

export function useSaveMemberDefaultLimit(orgId: string) {
  return useGovernanceWrite(
    orgId,
    ({ limit, version }: { limit: string | null; version: string }) =>
      orgGovernanceApi.putMemberDefault(orgId, limit, version),
  )
}

export function useSaveUserLimit(orgId: string) {
  return useGovernanceWrite(
    orgId,
    ({ userId, limit, version }: { userId: string; limit: string | null; version: string }) =>
      orgGovernanceApi.putUserLimit(orgId, userId, limit, version),
  )
}

export function useSaveOrgCatalog(orgId: string) {
  return useGovernanceWrite(
    orgId,
    ({ items, version }: { items: CatalogOverride[]; version: string }) =>
      orgGovernanceApi.putCatalog(orgId, items, version),
  )
}

export function useCreateInvitation(orgId: string) {
  return useGovernanceWrite(orgId, (data: InvitationCreate) =>
    orgGovernanceApi.createInvitation(orgId, data),
  )
}

export function useRevokeInvitation(orgId: string) {
  return useGovernanceWrite(orgId, (invitationId: string) =>
    orgGovernanceApi.revokeInvitation(orgId, invitationId),
  )
}

export function useResendInvitation(orgId: string) {
  return useGovernanceWrite(orgId, (invitationId: string) =>
    orgGovernanceApi.resendInvitation(orgId, invitationId),
  )
}
