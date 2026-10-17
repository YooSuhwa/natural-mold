'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { tenantAdminApi } from '@/lib/api/tenant-admin'
import { organizationQueryKeys } from '@/lib/query-keys/organizations'
import { tenantAdminKeys } from '@/lib/query-keys/tenant-admin'
import type {
  TenantInvitationCreate,
  TenantPoliciesUpdate,
  TenantRole,
} from '@/lib/types/tenant-admin'

/**
 * Every company write bumps the company version (`tenants.updated_at`), so it refreshes
 * the overview (the version source of S06 writes) and all company settings caches.
 */
export function useTenantMutation<Input, Result>(
  tenantId: string,
  run: (input: Input) => Promise<Result>,
  options: { refreshOrganizations?: boolean } = {},
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: run,
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: tenantAdminKeys.all(tenantId) }),
        queryClient.invalidateQueries({ queryKey: organizationQueryKeys.tenantOverview(tenantId) }),
        options.refreshOrganizations
          ? queryClient.invalidateQueries({ queryKey: organizationQueryKeys.mine })
          : null,
      ])
    },
  })
}

export function useRenameTenant(tenantId: string) {
  return useTenantMutation(
    tenantId,
    ({ name, version }: { name: string; version: string }) =>
      tenantAdminApi.rename(tenantId, name, version),
    { refreshOrganizations: true },
  )
}

export function useTenantPolicies(tenantId: string) {
  return useQuery({
    queryKey: tenantAdminKeys.policies(tenantId),
    queryFn: () => tenantAdminApi.getPolicies(tenantId),
    retry: false,
  })
}

export function useUpdateTenantPolicies(tenantId: string) {
  return useTenantMutation(
    tenantId,
    ({ data, version }: { data: TenantPoliciesUpdate; version: string }) =>
      tenantAdminApi.putPolicies(tenantId, data, version),
  )
}

export function usePublicationRequests(tenantId: string, cursor: string | null, enabled: boolean) {
  return useQuery({
    queryKey: tenantAdminKeys.publications(tenantId, cursor),
    queryFn: () => tenantAdminApi.listPublicationRequests(tenantId, cursor),
    enabled,
    retry: false,
  })
}

export function usePublicationDecisions(tenantId: string) {
  return {
    approve: useTenantMutation(tenantId, (itemId: string) =>
      tenantAdminApi.approvePublication(tenantId, itemId),
    ),
    reject: useTenantMutation(tenantId, ({ itemId, note }: { itemId: string; note: string }) =>
      tenantAdminApi.rejectPublication(tenantId, itemId, note),
    ),
  }
}

export function useTenantMembers(tenantId: string, cursor: string | null) {
  return useQuery({
    queryKey: tenantAdminKeys.members(tenantId, cursor),
    queryFn: () => tenantAdminApi.listMembers(tenantId, cursor),
    retry: false,
  })
}

interface Versioned {
  version: string
}

export function useTenantMemberMutations(tenantId: string) {
  return {
    setStatus: useTenantMutation(
      tenantId,
      (input: Versioned & { userId: string; status: 'active' | 'suspended' }) =>
        tenantAdminApi.setMemberStatus(tenantId, input.userId, input.status, input.version),
    ),
    remove: useTenantMutation(tenantId, (input: Versioned & { userId: string }) =>
      tenantAdminApi.removeMember(tenantId, input.userId, input.version),
    ),
    replaceRoles: useTenantMutation(
      tenantId,
      (input: Versioned & { userId: string; roles: TenantRole[] }) =>
        tenantAdminApi.replaceRoles(tenantId, input.userId, input.roles, input.version),
    ),
  }
}

export function useTenantInvitations(tenantId: string, cursor: string | null) {
  return useQuery({
    queryKey: tenantAdminKeys.invitations(tenantId, cursor),
    queryFn: () => tenantAdminApi.listInvitations(tenantId, cursor),
    retry: false,
  })
}

export function useTenantInvitationMutations(tenantId: string) {
  return {
    create: useTenantMutation(tenantId, (input: Versioned & { data: TenantInvitationCreate }) =>
      tenantAdminApi.createInvitation(tenantId, input.data, input.version),
    ),
    revoke: useTenantMutation(tenantId, (input: Versioned & { id: string }) =>
      tenantAdminApi.revokeInvitation(tenantId, input.id, input.version),
    ),
    resend: useTenantMutation(tenantId, (input: Versioned & { id: string }) =>
      tenantAdminApi.resendInvitation(tenantId, input.id, input.version),
    ),
  }
}
