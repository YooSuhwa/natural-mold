export const mcpServerQueryKeys = {
  all: ['mcp-servers'] as const,
  detail: (serverId: string | null | undefined) => ['mcp-servers', serverId] as const,
  allTools: ['mcp-tools', 'all'] as const,
  registry: ['mcp-server-types'] as const,
}
