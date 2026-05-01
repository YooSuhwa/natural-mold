'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { mcpApi } from '@/lib/api/mcp'
import type {
  McpFromRegistryRequest,
  McpImportRequest,
  McpProbeRequest,
  McpServerCreateRequest,
  McpServerUpdateRequest,
} from '@/lib/types/mcp'

const KEY_LIST = ['mcp-servers'] as const
const KEY_REGISTRY = ['mcp-server-types'] as const

export function useMcpServers() {
  return useQuery({
    queryKey: KEY_LIST,
    queryFn: mcpApi.list,
    staleTime: 30_000,
  })
}

export function useMcpServer(id: string | null | undefined) {
  return useQuery({
    queryKey: ['mcp-servers', id],
    queryFn: () => mcpApi.get(id!),
    enabled: !!id,
  })
}

export function useCreateMcpServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: McpServerCreateRequest) => mcpApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_LIST }),
  })
}

export function useUpdateMcpServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: McpServerUpdateRequest }) =>
      mcpApi.update(id, data),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: ['mcp-servers', id] })
    },
  })
}

export function useDeleteMcpServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => mcpApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_LIST }),
  })
}

export function useTestMcpServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => mcpApi.test(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: ['mcp-servers', id] })
    },
  })
}

export function useDiscoverMcpTools() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => mcpApi.discover(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: ['mcp-servers', id] })
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
    queryKey: ['mcp-tools', 'all'],
    queryFn: mcpApi.listAllTools,
    staleTime: 30_000,
  })
}

// -- M8: Registry -----------------------------------------------------------

/** One-click MCP server templates. Cached for 5 min — registry rarely changes. */
export function useMcpRegistry() {
  return useQuery({
    queryKey: KEY_REGISTRY,
    queryFn: mcpApi.listRegistry,
    staleTime: 5 * 60_000,
  })
}

export function useCreateFromRegistry() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: McpFromRegistryRequest) => mcpApi.createFromRegistry(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_LIST }),
  })
}

// -- Import / Export --------------------------------------------------------

/** Bulk-import MCP servers from a Claude Desktop config. Invalidates the
 *  list so newly-created rows appear immediately. */
export function useImportMcpServers() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: McpImportRequest) => mcpApi.importServers(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_LIST }),
  })
}

/** Lazy export — caller fetches on click and triggers a browser download. */
export function useExportMcpServers() {
  return useMutation({
    mutationFn: () => mcpApi.exportServers(),
  })
}
