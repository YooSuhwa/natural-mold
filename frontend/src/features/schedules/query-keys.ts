export const scheduleQueryKeys = {
  all: ['triggers'] as const,
  global: ['triggers', 'global'] as const,
  agent: (agentId: string) => ['triggers', 'agent', agentId] as const,
  runs: (triggerId: string) => ['trigger-runs', triggerId] as const,
}
