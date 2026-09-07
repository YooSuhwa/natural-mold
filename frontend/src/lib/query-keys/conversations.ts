export const conversationQueryKeys = {
  pageRoot: ['conversations', 'page'] as const,
  agentPageRoot: (agentId: string) => ['agents', agentId, 'conversations', 'page'] as const,
  prefix: (conversationId: string) => ['conversations', conversationId] as const,
  messages: (conversationId: string) => ['conversations', conversationId, 'messages'] as const,
  runMessageLinksLifecycle: (conversationId: string | null) =>
    ['conversations', conversationId ?? 'none', 'run-message-links-lifecycle'] as const,
  runMessageLinkIdentity: (conversationId: string | null, messageId: string | null | undefined) =>
    [
      'conversations',
      conversationId ?? 'none',
      'run-message-link-identity',
      messageId ?? 'none',
    ] as const,
  runMessageLinks: (
    conversationId: string | null,
    messageIdBatch: readonly string[],
    resolutionPhase: 'live' | 'terminal',
    terminalGeneration: number,
  ) =>
    [
      'conversations',
      conversationId ?? 'none',
      'run-message-links',
      messageIdBatch,
      resolutionPhase,
      terminalGeneration,
    ] as const,
}
