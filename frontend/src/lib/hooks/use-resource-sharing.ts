'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { grantsApi } from '@/lib/api/grants'
import { grantQueryKeys } from '@/lib/query-keys/grants'
import type { GrantCreate, OwnershipTransfer, ShareResourceType } from '@/lib/types/grant'

/** Resource list roots refreshed after any sharing change (owner badges, menus). */
const RESOURCE_ROOTS: Record<ShareResourceType, readonly string[]> = {
  agent: ['agents'],
  skill: ['skills'],
  tool: ['tools'],
  mcp_server: ['mcp-servers'],
  credential: ['credentials'],
}

export function useResourceAccess(type: ShareResourceType, id: string, enabled = true) {
  return useQuery({
    queryKey: grantQueryKeys.access(type, id),
    queryFn: () => grantsApi.access(type, id),
    enabled: enabled && !!id,
    retry: false,
  })
}

/** Grants of the resource plus its list root (share badges, menus, visibility). */
function useInvalidateSharing(type: ShareResourceType, id: string) {
  const qc = useQueryClient()
  return () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: grantQueryKeys.resource(type, id) }),
      qc.invalidateQueries({ queryKey: [...RESOURCE_ROOTS[type]] }),
    ])
}

export function useCreateGrant(type: ShareResourceType, id: string) {
  const invalidate = useInvalidateSharing(type, id)
  return useMutation({
    mutationFn: (body: GrantCreate) => grantsApi.create(type, id, body),
    onSuccess: invalidate,
  })
}

export function useRevokeGrant(type: ShareResourceType, id: string) {
  const invalidate = useInvalidateSharing(type, id)
  return useMutation({
    mutationFn: (grantId: string) => grantsApi.revoke(grantId),
    // A concurrent revoke (another tab) also lands here: refresh either way.
    onSettled: invalidate,
  })
}

export function useTransferOwnership(type: ShareResourceType, id: string) {
  const invalidate = useInvalidateSharing(type, id)
  return useMutation({
    mutationFn: (body: OwnershipTransfer) => grantsApi.transfer(type, id, body),
    onSuccess: invalidate,
  })
}
