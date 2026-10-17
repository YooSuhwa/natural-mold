'use client'

import { useCallback, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

import {
  RESOURCE_PERMISSION_BATCH,
  resourcePermissionsApi,
  type PermissionResourceType,
} from '@/lib/api/resource-permissions'
import { useActiveOrganization } from '@/lib/hooks/use-organizations'
import { grantQueryKeys } from '@/lib/query-keys/grants'
import type { OrgCapability } from '@/lib/types/organization'

/**
 * `my_permissions` of the given rows (plan 13.1 `usePermission`). Under `['grants', type]`,
 * so sharing changes and organization switches drop it with the grant caches.
 * `permissionsOf(id)` is `undefined` until answered, on errors and for rows the server
 * left out — callers then fail closed (owner rule only, `useResourceAccess`).
 */
export function useResourcePermissions(
  type: PermissionResourceType,
  ids: readonly (string | null | undefined)[],
) {
  const unique = useMemo(
    () => Array.from(new Set(ids.filter((id): id is string => !!id))).sort(),
    [ids],
  )
  const signature = unique.join(',')
  const query = useQuery({
    queryKey: grantQueryKeys.permissions(type, signature),
    queryFn: async () => {
      const answered = new Map<string, readonly string[]>()
      for (let i = 0; i < unique.length; i += RESOURCE_PERMISSION_BATCH) {
        const page = await resourcePermissionsApi.list(
          type,
          unique.slice(i, i + RESOURCE_PERMISSION_BATCH),
        )
        for (const item of page.items) answered.set(item.id, item.my_permissions)
      }
      return answered
    },
    enabled: unique.length > 0,
    staleTime: 30_000,
  })
  const data = query.data
  const permissionsOf = useCallback(
    (id: string | null | undefined): readonly string[] | undefined =>
      id ? data?.get(id) : undefined,
    [data],
  )
  return {
    permissionsOf,
    isLoading: query.isLoading,
    /** True once the server answered every page (a missing id is then hidden). */
    answered: query.isSuccess,
    error: query.error,
    refetch: query.refetch,
  }
}

/**
 * Organization capability (`cap_*`, plan 13.1 `useCapability`). While capabilities are not
 * enforced (legacy/shadow) every member may create, so this answers `true`; unknown
 * (still loading) also answers `true` — the server decides.
 */
export function useCapability(capability: OrgCapability): boolean {
  const { activeOrganization } = useActiveOrganization()
  if (!activeOrganization?.capabilities_enforced) return true
  return activeOrganization.capabilities.includes(capability)
}
