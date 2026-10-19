import type { UsageLimitView } from '@/lib/types/org-governance'
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
    listGroups: vi.fn(),
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

export const governanceApiMock = {
  orgGovernanceApi: {
    getRetention: vi.fn(),
    putRetention: vi.fn(),
    getLimits: vi.fn(),
    putMemberDefault: vi.fn(),
    getUserLimit: vi.fn(),
    putUserLimit: vi.fn(),
    getCatalog: vi.fn(),
    putCatalog: vi.fn(),
    catalogImpact: vi.fn(),
    listInvitations: vi.fn(),
    createInvitation: vi.fn(),
    revokeInvitation: vi.fn(),
    resendInvitation: vi.fn(),
  },
}

export function makeLimitView(overrides: Partial<UsageLimitView> = {}): UsageLimitView {
  return {
    scope: 'org',
    id: 'org-1',
    org_id: 'org-1',
    monthly_spend_limit: '100',
    effective_limit: '100',
    used: '12.5',
    remaining: '87.5',
    resets_at: '2026-11-01T00:00:00Z',
    parent_limit: '500',
    allocated: null,
    default_member_limit: null,
    ...overrides,
  }
}

/** Defaults for screens that render governance sections next to the one under test. */
export function resetGovernanceMocks(): void {
  const api = governanceApiMock.orgGovernanceApi
  api.getRetention.mockResolvedValue({
    conversation_days: null,
    agent_trash_days: 30,
    removed_member_days: 30,
    updated_at: '2026-10-16T00:00:00Z',
  })
  api.getLimits.mockResolvedValue(makeLimitView())
  api.listInvitations.mockResolvedValue({
    items: [],
    next_cursor: null,
    has_more: false,
    updated_at: '2026-10-16T00:00:00Z',
  })
}
