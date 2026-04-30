import { apiFetch } from './client'
import type {
  McpDiscoverResult,
  McpFromRegistryRequest,
  McpRegistryEntry,
  McpServer,
  McpServerCreateRequest,
  McpServerDetail,
  McpServerUpdateRequest,
  McpTestResult,
} from '@/lib/types/mcp'

export const mcpApi = {
  list: () => apiFetch<McpServer[]>('/api/mcp-servers'),
  get: (id: string) => apiFetch<McpServerDetail>(`/api/mcp-servers/${id}`),
  create: (data: McpServerCreateRequest) =>
    apiFetch<McpServer>('/api/mcp-servers', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  update: (id: string, data: McpServerUpdateRequest) =>
    apiFetch<McpServer>(`/api/mcp-servers/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  delete: (id: string) =>
    apiFetch<void>(`/api/mcp-servers/${id}`, { method: 'DELETE' }),
  test: (id: string) =>
    apiFetch<McpTestResult>(`/api/mcp-servers/${id}/test`, { method: 'POST' }),
  discover: (id: string) =>
    apiFetch<McpDiscoverResult>(`/api/mcp-servers/${id}/discover`, {
      method: 'POST',
    }),

  // -- M8: Registry -----------------------------------------------------------
  /** Catalog of one-click MCP server templates (icon + transport + env vars). */
  listRegistry: () => apiFetch<McpRegistryEntry[]>('/api/mcp-server-types'),
  /** Materialize a registry entry as a real MCP server row. */
  createFromRegistry: (data: McpFromRegistryRequest) =>
    apiFetch<McpServer>('/api/mcp-servers/from-registry', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
}
