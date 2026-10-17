/** `['platform-tenants', ...]`: platform scope, independent of the active organization. */
export const platformTenantQueryKeys = {
  all: ['platform-tenants'] as const,
  installation: () => ['installation'] as const,
  list: (status: string | null, search: string, cursor: string | null) =>
    ['platform-tenants', 'list', status, search, cursor] as const,
  detail: (id: string) => ['platform-tenants', 'detail', id] as const,
  usage: (id: string) => ['platform-tenants', 'usage', id] as const,
  deletion: (id: string) => ['platform-tenants', 'deletion', id] as const,
  exports: (id: string) => ['platform-tenants', 'exports', id] as const,
}
