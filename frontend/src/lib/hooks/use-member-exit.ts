'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { memberExitApi } from '@/lib/api/member-exit'
import { agentQueryKeys } from '@/lib/query-keys/agents'
import { credentialQueryKeys } from '@/lib/query-keys/credentials'
import { grantQueryKeys } from '@/lib/query-keys/grants'
import { mcpServerQueryKeys } from '@/lib/query-keys/mcp-servers'
import { skillQueryKeys } from '@/lib/query-keys/skills'
import { toolQueryKeys } from '@/lib/query-keys/tools'
import { memberExitQueryKeys } from '@/lib/query-keys/member-exit'
import { organizationQueryKeys } from '@/lib/query-keys/organizations'
import type { MemberRemoval } from '@/lib/types/member-exit'

export function useMemberExitPreview(orgId: string, userId: string) {
  return useQuery({
    queryKey: memberExitQueryKeys.preview(orgId, userId),
    queryFn: () => memberExitApi.preview(orgId, userId),
    // A stale preview would settle the wrong resources: always re-read on open.
    staleTime: 0,
    retry: false,
  })
}

/**
 * Removal transfers resources and revokes grants, so on success and failure
 * (stale_version, a late share) the org subtree, resource lists and the
 * caller's own organization list are refetched.
 */
export function useRemoveMemberWithSettlement(orgId: string, userId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ data, version }: { data: MemberRemoval; version: string }) =>
      memberExitApi.remove(orgId, userId, data, version),
    onSettled: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: organizationQueryKeys.orgAll(orgId) }),
        qc.invalidateQueries({ queryKey: organizationQueryKeys.mine }),
        qc.invalidateQueries({ queryKey: grantQueryKeys.all }),
        ...[
          agentQueryKeys.all,
          skillQueryKeys.all,
          toolQueryKeys.all,
          mcpServerQueryKeys.all,
          credentialQueryKeys.all,
        ].map((queryKey) => qc.invalidateQueries({ queryKey })),
      ]),
  })
}
