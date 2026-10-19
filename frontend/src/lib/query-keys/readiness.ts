/** `['readiness', agentId]` (plan 13.1); summaries per agent under the same root. */
export const readinessQueryKeys = {
  all: ['readiness'] as const,
  detail: (agentId: string) => ['readiness', agentId] as const,
  summary: (agentId: string) => ['readiness', 'summary', agentId] as const,
}

export const credentialBindingQueryKeys = {
  all: ['credential-bindings'] as const,
}
