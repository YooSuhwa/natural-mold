'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { mcpApi } from '@/lib/api/mcp'
import { mcpServerQueryKeys } from '@/lib/query-keys/mcp-servers'

/** People and teams kept from one MCP tool (server managers only). */
export function useMcpToolBlocks(serverId: string, toolId: string) {
  return useQuery({
    queryKey: mcpServerQueryKeys.toolBlocks(serverId, toolId),
    queryFn: () => mcpApi.listToolBlocks(serverId, toolId),
  })
}

export function useBlockMcpTool(serverId: string, toolId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (principal: { type: 'user' | 'group'; id: string }) =>
      mcpApi.blockTool(serverId, toolId, {
        principal_type: principal.type,
        principal_id: principal.id,
      }),
    onSettled: () =>
      qc.invalidateQueries({ queryKey: mcpServerQueryKeys.toolBlocks(serverId, toolId) }),
  })
}

export function useUnblockMcpTool(serverId: string, toolId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (grantId: string) => mcpApi.unblockTool(serverId, toolId, grantId),
    onSettled: () =>
      qc.invalidateQueries({ queryKey: mcpServerQueryKeys.toolBlocks(serverId, toolId) }),
  })
}
