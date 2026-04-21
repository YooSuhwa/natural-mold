'use client'

import { useMemo } from 'react'
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { toolsApi } from '@/lib/api/tools'
import type {
  Connection,
  MCPServerCreateRequest,
  MCPServerUpdateRequest,
  Tool,
  ToolCustomCreateRequest,
} from '@/lib/types'

// MCP register/delete may add or remove rows in the tools table (cascade).
// PATCH does not touch tools rows, so callers should use the narrower
// invalidator below to avoid an unnecessary tools refetch.
function invalidateMCPAndTools(qc: QueryClient) {
  qc.invalidateQueries({ queryKey: ['mcp-servers'] })
  qc.invalidateQueries({ queryKey: ['tools'] })
}

export function useTools() {
  return useQuery({ queryKey: ['tools'], queryFn: toolsApi.list, staleTime: 60000 })
}

export function useCreateCustomTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: ToolCustomCreateRequest) => toolsApi.createCustom(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tools'] }),
  })
}

export function useRegisterMCPServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: MCPServerCreateRequest) => toolsApi.registerMCPServer(data),
    onSuccess: () => invalidateMCPAndTools(qc),
  })
}

export function useDeleteTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => toolsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tools'] }),
  })
}

export function useMCPServers() {
  return useQuery({
    queryKey: ['mcp-servers'],
    queryFn: toolsApi.listMCPServers,
    staleTime: 60000,
  })
}

/**
 * Connection 기준으로 사용 중 tool 목록을 파생한다 — /connections 페이지의 카드
 * 카운트와 삭제 가드에 사용.
 * - PREBUILT: tool.provider_name 매칭. system tool은 `tool.connection_id`를 갖지 않고
 *   `user_id + type + provider_name` default connection이 SOT (M3 invariant).
 * - CUSTOM: tool.connection_id 매칭 (M4 SOT).
 * - MCP: `mcp_server.credential_id === connection.credential_id` 이중 hop (ADR-008 N:1).
 */
export function useToolsByConnection(connection: Connection): Tool[] {
  const { data: tools } = useTools()
  const { data: mcpServers } = useMCPServers()
  return useMemo(() => {
    if (!tools) return []
    if (connection.type === 'mcp') {
      if (!mcpServers || !connection.credential_id) return []
      const serverIds = new Set(
        mcpServers
          .filter((s) => s.credential_id === connection.credential_id)
          .map((s) => s.id),
      )
      return tools.filter((t) => t.mcp_server_id && serverIds.has(t.mcp_server_id))
    }
    if (connection.type === 'prebuilt') {
      // PREBUILT runtime은 provider별 default connection만 사용. non-default row는
      // 어떤 tool에도 attach되지 않으므로 사용량 0 — 삭제 가드도 풀린다.
      if (!connection.is_default) return []
      return tools.filter(
        (t) => t.type === 'prebuilt' && t.provider_name === connection.provider_name,
      )
    }
    return tools.filter((t) => t.connection_id === connection.id)
  }, [tools, mcpServers, connection])
}

export function useUpdateMCPServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: MCPServerUpdateRequest }) =>
      toolsApi.updateMCPServer(id, data),
    // PATCH only mutates the server row (name/credential/auth_config).
    // No tools row changes, so skip the broader ['tools'] invalidation.
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mcp-servers'] }),
  })
}

export function useDeleteMCPServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => toolsApi.deleteMCPServer(id),
    onSuccess: () => invalidateMCPAndTools(qc),
  })
}
