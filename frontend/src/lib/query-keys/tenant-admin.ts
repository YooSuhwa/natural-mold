/** Company settings caches (S05–S07). Keyed by company; an org switch purges them (13.11). */
export const tenantAdminKeys = {
  all: (tenantId: string) => ['tenant-admin', tenantId] as const,
  policies: (tenantId: string) => ['tenant-admin', tenantId, 'policies'] as const,
  publications: (tenantId: string, cursor: string | null) =>
    ['tenant-admin', tenantId, 'publication-requests', cursor] as const,
  members: (tenantId: string, cursor: string | null) =>
    ['tenant-admin', tenantId, 'members', cursor] as const,
  invitations: (tenantId: string, cursor: string | null) =>
    ['tenant-admin', tenantId, 'invitations', cursor] as const,
  models: (tenantId: string) => ['tenant-admin', tenantId, 'models'] as const,
  credentials: (tenantId: string) => ['tenant-admin', tenantId, 'credentials'] as const,
  catalog: (tenantId: string) => ['tenant-admin', tenantId, 'catalog-policy'] as const,
  llm: (tenantId: string) => ['tenant-admin', tenantId, 'llm-settings'] as const,
}
