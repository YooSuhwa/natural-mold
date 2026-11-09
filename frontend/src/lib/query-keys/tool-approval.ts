export const toolApprovalQueryKeys = {
  all: ['tool-approval'] as const,
  view: (agentId: string) => ['tool-approval', 'view', agentId] as const,
  grants: (conversationId: string) => ['tool-approval', 'grants', conversationId] as const,
}
