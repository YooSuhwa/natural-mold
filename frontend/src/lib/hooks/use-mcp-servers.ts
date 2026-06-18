'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { mcpApi } from '@/lib/api/mcp'
import { mcpServerQueryKeys } from '@/lib/query-keys/mcp-servers'
import { requiredQueryValue } from './required-query-value'
import type {
  McpFromRegistryRequest,
  McpImportRequest,
  McpProbeRequest,
  McpServerCreateRequest,
  McpServerUpdateRequest,
} from '@/lib/types/mcp'

export function useMcpServers() {
  return useQuery({
    queryKey: mcpServerQueryKeys.all,
    queryFn: mcpApi.list,
    staleTime: 30_000,
  })
}

export function useMcpServer(id: string | null | undefined) {
  return useQuery({
    queryKey: mcpServerQueryKeys.detail(id),
    queryFn: () => mcpApi.get(requiredQueryValue(id, 'MCP server id')),
    enabled: !!id,
  })
}

export function useCreateMcpServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: McpServerCreateRequest) => mcpApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: mcpServerQueryKeys.all }),
  })
}

export function useUpdateMcpServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: McpServerUpdateRequest }) =>
      mcpApi.update(id, data),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: mcpServerQueryKeys.all })
      qc.invalidateQueries({ queryKey: mcpServerQueryKeys.detail(id) })
    },
  })
}

export function useDeleteMcpServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => mcpApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: mcpServerQueryKeys.all }),
  })
}

export function useTestMcpServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => mcpApi.test(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: mcpServerQueryKeys.all })
      qc.invalidateQueries({ queryKey: mcpServerQueryKeys.detail(id) })
    },
  })
}

export function useDiscoverMcpTools() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => mcpApi.discover(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: mcpServerQueryKeys.all })
      qc.invalidateQueries({ queryKey: mcpServerQueryKeys.detail(id) })
    },
  })
}

export function useProbeMcpServer() {
  return useMutation({
    mutationFn: (data: McpProbeRequest) => mcpApi.probe(data),
  })
}

export function useAllMcpTools() {
  return useQuery({
    queryKey: mcpServerQueryKeys.allTools,
    queryFn: mcpApi.listAllTools,
    staleTime: 30_000,
  })
}

// -- M8: Registry -----------------------------------------------------------

/** One-click MCP server templates. Cached for 5 min — registry rarely changes. */
export function useMcpRegistry() {
  return useQuery({
    queryKey: mcpServerQueryKeys.registry,
    queryFn: mcpApi.listRegistry,
    staleTime: 5 * 60_000,
  })
}

export function useCreateFromRegistry() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: McpFromRegistryRequest) => mcpApi.createFromRegistry(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: mcpServerQueryKeys.all }),
  })
}

// -- Import / Export --------------------------------------------------------

/** Bulk-import MCP servers from a Claude Desktop config. Invalidates the
 *  list so newly-created rows appear immediately. */
export function useImportMcpServers() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: McpImportRequest) => mcpApi.importServers(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: mcpServerQueryKeys.all }),
  })
}

/** Lazy export — caller fetches on click and triggers a browser download. */
export function useExportMcpServers() {
  return useMutation({
    mutationFn: () => mcpApi.exportServers(),
  })
}
