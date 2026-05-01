import { apiFetch } from './client'
import type {
  McpDiscoverResult,
  McpExportResponse,
  McpFromRegistryRequest,
  McpImportRequest,
  McpImportResult,
  McpProbeRequest,
  McpProbeResult,
  McpRegistryEntry,
  McpServer,
  McpServerCreateRequest,
  McpServerDetail,
  McpServerUpdateRequest,
  McpTestResult,
  McpToolWithServer,
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
  /** Preview a server's tool list without creating any DB rows. */
  probe: (data: McpProbeRequest) =>
    apiFetch<McpProbeResult>('/api/mcp-servers/probe', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  /** Flat list of every MCP tool the user owns across all servers — used
   *  by the unified agent Tools picker (MCP tab). */
  listAllTools: () =>
    apiFetch<McpToolWithServer[]>('/api/mcp-servers/all-tools'),

  // -- M8: Registry -----------------------------------------------------------
  /** Catalog of one-click MCP server templates (icon + transport + env vars). */
  listRegistry: () => apiFetch<McpRegistryEntry[]>('/api/mcp-server-types'),
  /** Materialize a registry entry as a real MCP server row. */
  createFromRegistry: (data: McpFromRegistryRequest) =>
    apiFetch<McpServer>('/api/mcp-servers/from-registry', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // -- Import / Export (Claude Desktop compatible) -------------------------
  /** Bulk-create or upsert MCP servers from a `{mcpServers:{...}}` payload. */
  importServers: (payload: McpImportRequest) =>
    apiFetch<McpImportResult>('/api/mcp-servers/import', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** Dump all owned MCP servers as a Claude Desktop-compatible JSON. Secrets
   *  are NOT included — `credential_id` references survive but resolved
   *  values do not. */
  exportServers: () =>
    apiFetch<McpExportResponse>('/api/mcp-servers/export'),
}
