'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { grantsApi } from '@/lib/api/grants'
import { agentQueryKeys } from '@/lib/query-keys/agents'
import { credentialQueryKeys } from '@/lib/query-keys/credentials'
import { grantQueryKeys } from '@/lib/query-keys/grants'
import { mcpServerQueryKeys } from '@/lib/query-keys/mcp-servers'
import { skillQueryKeys } from '@/lib/query-keys/skills'
import { toolQueryKeys } from '@/lib/query-keys/tools'
import type { GrantCreate, OwnershipTransfer, ShareResourceType } from '@/lib/types/grant'

/** Resource list roots refreshed after any sharing change (owner badges, menus). */
const RESOURCE_ROOTS: Record<ShareResourceType, readonly string[]> = {
  agent: agentQueryKeys.all,
  skill: skillQueryKeys.all,
  tool: toolQueryKeys.all,
  mcp_server: mcpServerQueryKeys.all,
  credential: credentialQueryKeys.all,
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
      qc.invalidateQueries({ queryKey: RESOURCE_ROOTS[type] }),
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
