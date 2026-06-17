export const agentQueryKeys = {
  all: ['agents'] as const,
  summary: ['agents', 'summary'] as const,
  detail: (agentId: string) => ['agents', agentId] as const,
}
