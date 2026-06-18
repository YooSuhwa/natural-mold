export const conversationQueryKeys = {
  pageRoot: ['conversations', 'page'] as const,
  agentPageRoot: (agentId: string) => ['agents', agentId, 'conversations', 'page'] as const,
  prefix: (conversationId: string) => ['conversations', conversationId] as const,
  messages: (conversationId: string) => ['conversations', conversationId, 'messages'] as const,
}
