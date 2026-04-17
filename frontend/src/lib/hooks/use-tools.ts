'use client'

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { toolsApi } from '@/lib/api/tools'
import type {
  MCPServerCreateRequest,
  MCPServerUpdateRequest,
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

export function useUpdateToolAuthConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      authConfig,
      credentialId,
    }: {
      id: string
      authConfig: Record<string, unknown>
      credentialId?: string | null
    }) => toolsApi.updateAuthConfig(id, authConfig, credentialId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tools'] }),
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
