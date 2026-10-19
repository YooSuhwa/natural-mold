import type { TenantOverviewDetail } from '@/lib/types/tenant-admin'

/** `vi.mock` factories for the company settings APIs (S05–S07). */
export const tenantAdminApiMock = {
  tenantAdminApi: {
    rename: vi.fn(),
    getPolicies: vi.fn(),
    putPolicies: vi.fn(),
    listPublicationRequests: vi.fn(),
    approvePublication: vi.fn(),
    rejectPublication: vi.fn(),
    listMembers: vi.fn(),
    setMemberStatus: vi.fn(),
    removeMember: vi.fn(),
    replaceRoles: vi.fn(),
    listInvitations: vi.fn(),
    createInvitation: vi.fn(),
    revokeInvitation: vi.fn(),
    resendInvitation: vi.fn(),
  },
}

export const tenantCatalogApiMock = {
  tenantCatalogApi: {
    listModels: vi.fn(),
    createModel: vi.fn(),
    updateModel: vi.fn(),
    deleteModel: vi.fn(),
    listCredentials: vi.fn(),
    createCredential: vi.fn(),
    updateCredential: vi.fn(),
    deleteCredential: vi.fn(),
    getCatalogPolicy: vi.fn(),
    putCatalogPolicy: vi.fn(),
    getLlmSettings: vi.fn(),
    putLlmSetting: vi.fn(),
  },
}

export const VERSION = '2026-10-18T01:02:03.456789Z'

export function makeOverview(
  access: TenantOverviewDetail['access'],
  overrides: Partial<TenantOverviewDetail> = {},
): TenantOverviewDetail {
  const counts =
    access === 'member'
      ? {}
      : {
          active_member_count: 12,
          organization_count: 3,
          ...(access === 'admin' ? { active_admin_count: 2 } : {}),
        }
  return {
    id: 'tenant-1',
    name: 'Hancom',
    slug: 'hancom',
    status: 'active',
    updated_at: VERSION,
    access,
    ...counts,
    ...overrides,
  }
}

/** A promise that never settles: keeps a query in its loading state. */
export function pending<T>(): Promise<T> {
  return new Promise<T>(() => undefined)
}
