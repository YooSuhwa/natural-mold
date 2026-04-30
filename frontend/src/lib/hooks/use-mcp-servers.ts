'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { mcpApi } from '@/lib/api/mcp'
import type {
  McpFromRegistryRequest,
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
