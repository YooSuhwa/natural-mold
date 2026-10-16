import type { OrganizationView } from '@/lib/types/organization'

export const orgApiMock = {
  tenantsApi: { overview: vi.fn() },
  meOrganizationsApi: { list: vi.fn(), setActive: vi.fn() },
  tenantOrganizationsApi: {
    list: vi.fn(),
    create: vi.fn(),
    rename: vi.fn(),
    archive: vi.fn(),
    restore: vi.fn(),
  },
  orgAdminApi: {
    get: vi.fn(),
    update: vi.fn(),
    listMembers: vi.fn(),
    addMember: vi.fn(),
    setMemberStatus: vi.fn(),
    removeMember: vi.fn(),
    searchPrincipals: vi.fn(),
    listRoles: vi.fn(),
    replaceRoles: vi.fn(),
    listCapabilities: vi.fn(),
    replaceCapabilities: vi.fn(),
  },
}

export function makeOrgView(overrides: Partial<OrganizationView> = {}): OrganizationView {
  return {
    id: 'org-1',
    tenant_id: 'tenant-1',
    name: 'Alpha',
    slug: 'alpha',
    status: 'active',
    is_default: false,
    settings: {
      default_member_role: 'member',
      allow_public_share_links: true,
      registration_policy: null,
    },
    updated_at: '2026-10-16T00:00:00Z',
    permissions: ['can_manage_members', 'can_manage_policy'],
    ...overrides,
  }
}
