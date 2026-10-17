'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { tenantSsoApi } from '@/lib/api/sso'
import type { LdapConnectionInput, OidcConnectionInput, SsoMode } from '@/lib/types/sso'

/** S30 company SSO settings (plan 9.7; H-08). */
export const tenantSsoKeys = {
  all: (tenantId: string) => ['tenant-sso', tenantId] as const,
  domains: (tenantId: string) => ['tenant-sso', tenantId, 'domains'] as const,
  connections: (tenantId: string) => ['tenant-sso', tenantId, 'connections'] as const,
  policy: (tenantId: string) => ['tenant-sso', tenantId, 'policy'] as const,
}

export function useTenantDomains(tenantId: string) {
  return useQuery({
    queryKey: tenantSsoKeys.domains(tenantId),
    queryFn: () => tenantSsoApi.listDomains(tenantId),
    retry: false,
  })
}

export function useTenantSsoConnections(tenantId: string) {
  return useQuery({
    queryKey: tenantSsoKeys.connections(tenantId),
    queryFn: () => tenantSsoApi.listConnections(tenantId),
    retry: false,
  })
}

export function useTenantSsoPolicy(tenantId: string) {
  return useQuery({
    queryKey: tenantSsoKeys.policy(tenantId),
    queryFn: () => tenantSsoApi.getPolicy(tenantId),
    retry: false,
  })
}

/** Every S30 write invalidates the whole company SSO cache (domains gate the policy). */
function useSsoMutation<T, R>(tenantId: string, run: (input: T) => Promise<R>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: run,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: tenantSsoKeys.all(tenantId) }),
  })
}

export function useTenantSsoMutations(tenantId: string) {
  return {
    addDomain: useSsoMutation(tenantId, (domain: string) =>
      tenantSsoApi.addDomain(tenantId, domain),
    ),
    verifyDomain: useSsoMutation(tenantId, (id: string) => tenantSsoApi.verifyDomain(tenantId, id)),
    removeDomain: useSsoMutation(tenantId, (id: string) => tenantSsoApi.removeDomain(tenantId, id)),
    createConnection: useSsoMutation(tenantId, (input: OidcConnectionInput | LdapConnectionInput) =>
      tenantSsoApi.createConnection(tenantId, input),
    ),
    testConnection: useSsoMutation(
      tenantId,
      (input: { id: string; account?: { username: string; password: string } }) =>
        tenantSsoApi.testConnection(tenantId, input.id, input.account),
    ),
    activateConnection: useSsoMutation(tenantId, (id: string) =>
      tenantSsoApi.activateConnection(tenantId, id),
    ),
    deleteConnection: useSsoMutation(tenantId, (id: string) =>
      tenantSsoApi.deleteConnection(tenantId, id),
    ),
    putPolicy: useSsoMutation(tenantId, (input: { mode: SsoMode; breakGlass: string[] }) =>
      tenantSsoApi.putPolicy(tenantId, input.mode, input.breakGlass),
    ),
  }
}
