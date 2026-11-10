'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { mcpApi } from '@/lib/api/mcp'
import { toolsApi } from '@/lib/api/tools'
import { approvalQueryKeys } from '@/lib/query-keys/approvals'
import { mcpServerQueryKeys } from '@/lib/query-keys/mcp-servers'
import { toolQueryKeys } from '@/lib/query-keys/tools'
import type { RegistrationResourceType } from '@/lib/types/approval'

function statusKey(type: RegistrationResourceType, id: string) {
  return type === 'mcp_server'
    ? mcpServerQueryKeys.registration(id)
    : toolQueryKeys.registration(id)
}

/** Whether a tool or MCP server may be used yet (its editors only: `enabled`). */
export function useRegistrationStatus(
  type: RegistrationResourceType,
  id: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: statusKey(type, id),
    queryFn: () => (type === 'mcp_server' ? mcpApi.registration(id) : toolsApi.registration(id)),
    enabled: enabled && !!id,
  })
}

/** Ask the administrators again (after a rejection, or when no request stands). */
export function useRequestRegistration(type: RegistrationResourceType, id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () =>
      type === 'mcp_server' ? mcpApi.requestRegistration(id) : toolsApi.requestRegistration(id),
    onSuccess: (status) => qc.setQueryData(statusKey(type, id), status),
    onSettled: () =>
      Promise.all([
        qc.invalidateQueries({
          queryKey: type === 'mcp_server' ? mcpServerQueryKeys.all : toolQueryKeys.all,
        }),
        qc.invalidateQueries({ queryKey: approvalQueryKeys.all }),
      ]),
  })
}
