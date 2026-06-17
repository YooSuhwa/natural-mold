export const credentialQueryKeys = {
  all: ['credentials'] as const,
  detail: (credentialId: string) => ['credentials', credentialId] as const,
}
