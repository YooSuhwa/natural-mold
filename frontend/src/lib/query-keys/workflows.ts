/** Dropped on an organization switch: workflows belong to one organization. */
export const workflowQueryKeys = {
  all: ['workflows'] as const,
  list: ['workflows', 'list'] as const,
  detail: (id: string) => ['workflows', 'detail', id] as const,
  issues: (id: string, revision: number) => ['workflows', 'issues', id, revision] as const,
  versions: (id: string) => ['workflows', 'versions', id] as const,
  version: (id: string, versionNo: number) => ['workflows', 'version', id, versionNo] as const,
  runs: (id: string) => ['workflows', 'runs', id] as const,
  run: (runId: string) => ['workflows', 'run', runId] as const,
  usingAgent: (agentId: string) => ['workflows', 'using-agent', agentId] as const,
  agentTools: (agentId: string) => ['workflows', 'agent-tools', agentId] as const,
  schedules: (id: string) => ['workflows', 'schedules', id] as const,
  webhooks: (id: string) => ['workflows', 'webhooks', id] as const,
  toolCallRun: (conversationId: string, toolCallId: string) =>
    ['workflows', 'tool-call-run', conversationId, toolCallId] as const,
}
