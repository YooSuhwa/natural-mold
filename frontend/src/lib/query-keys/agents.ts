export const agentQueryKeys = {
  all: ['agents'] as const,
  summary: ['agents', 'summary'] as const,
  detail: (agentId: string | null | undefined) => ['agents', agentId] as const,
}
