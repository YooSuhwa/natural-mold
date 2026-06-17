import type { HealthTargetKind } from '@/lib/types/health'

export const healthQueryKeys = {
  models: ['health', 'models'] as const,
  mcpServers: ['health', 'mcp-servers'] as const,
  history: (targetKind: HealthTargetKind, targetId: string | null | undefined, limit?: number) =>
    limit === undefined
      ? (['health', 'history', targetKind, targetId] as const)
      : (['health', 'history', targetKind, targetId, limit] as const),
}
