import type { OrganizationSummary } from '@/lib/types/organization'

export function makeOrganization(
  overrides: Partial<OrganizationSummary> = {},
): OrganizationSummary {
  return {
    id: 'org-1',
    name: 'Alpha',
    slug: 'alpha',
    tenant_id: 'tenant-1',
    tenant_name: 'Hancom',
    tenant_status: 'active',
    is_default: true,
    roles: ['admin'],
    capabilities: [],
    ...overrides,
  }
}
