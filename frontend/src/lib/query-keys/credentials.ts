export const credentialQueryKeys = {
  all: ['credentials'] as const,
  detail: (credentialId: string | null | undefined) => ['credentials', credentialId] as const,
  types: ['credential-types'] as const,
  typeDetail: (definitionKey: string | null | undefined) =>
    ['credential-types', definitionKey] as const,
  auditLogs: (credentialId: string | null | undefined, limit?: number) =>
    limit === undefined
      ? (['credential-audit-logs', credentialId] as const)
      : (['credential-audit-logs', credentialId, limit] as const),
  systemAll: ['system-credentials'] as const,
}
