export const mcpServerQueryKeys = {
  all: ['mcp-servers'] as const,
  detail: (serverId: string | null | undefined) => ['mcp-servers', serverId] as const,
  registration: (serverId: string) => ['mcp-servers', serverId, 'registration'] as const,
  toolBlocks: (serverId: string, toolId: string) =>
    ['mcp-servers', serverId, 'tool-blocks', toolId] as const,
  allTools: ['mcp-tools', 'all'] as const,
  registry: ['mcp-server-types'] as const,
}
