// MCP domain types — mirrors backend `app/schemas/mcp.py`.

export type McpTransport = 'stdio' | 'sse' | 'streamable_http'
export type McpStatus = 'unknown' | 'connected' | 'auth_needed' | 'unreachable' | 'disabled'

export interface McpServer {
  id: string
  user_id: string
  name: string
  description: string | null
  transport: McpTransport | string
  url: string | null
  command: string | null
  args: unknown[]
  env_vars: Record<string, unknown>
  headers: Record<string, unknown>
  credential_id: string | null
  status: McpStatus | string
  last_pinged_at: string | null
  last_tool_count: number | null
  last_error: string | null
  created_at: string
  updated_at: string
}

export interface McpTool {
  id: string
  server_id: string
  name: string
  description: string | null
  input_schema: Record<string, unknown>
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface McpServerDetail extends McpServer {
  tools: McpTool[]
}

export interface McpServerCreateRequest {
  name: string
  description?: string | null
  transport: McpTransport
  url?: string | null
  command?: string | null
  args?: unknown[]
  env_vars?: Record<string, unknown>
  headers?: Record<string, unknown>
  credential_id?: string | null
}

export interface McpServerUpdateRequest {
  name?: string
  description?: string | null
  transport?: McpTransport
  url?: string | null
  command?: string | null
  args?: unknown[]
  env_vars?: Record<string, unknown>
  headers?: Record<string, unknown>
  credential_id?: string | null
  status?: McpStatus
}

export interface McpTestResult {
  success: boolean
  status: string
  server_info: Record<string, unknown>
  tool_count: number
  error: string | null
}

export interface McpDiscoverResult {
  success: boolean
  status: string
  tools: McpTool[]
  error: string | null
}

// -- Probe (preview without persistence, used by the wizard) ----------------

export interface McpProbeRequest {
  transport?: McpTransport
  url?: string | null
  command?: string | null
  headers?: Record<string, unknown>
  credential_id?: string | null
  registry_key?: string | null
}

export interface McpProbeTool {
  name: string
  description: string | null
  input_schema: Record<string, unknown>
}

export interface McpProbeResult {
  success: boolean
  server_info: Record<string, unknown>
  tools: McpProbeTool[]
  error: string | null
}

export interface McpServerWizardData {
  name: string
  description: string
  transport: McpTransport
  url: string
  command: string
  args: string[]
  credential_id: string | null
  env_vars: Record<string, string>
  headers: Record<string, string>
}

// -- M8: Server-Type Registry (one-click catalog) ---------------------------

export interface McpRegistryEntry {
  key: string
  display_name: string
  description: string | null
  icon_id: string | null
  transport: McpTransport
  url: string | null
  command: string | null
  args: string[] | null
  env_vars: Record<string, string>
  credential_definition_key: string | null
  documentation_url: string | null
}

export interface McpFromRegistryRequest {
  registry_key: string
  name: string
  credential_id?: string | null
}
