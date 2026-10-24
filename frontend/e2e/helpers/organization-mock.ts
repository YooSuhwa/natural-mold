/**
 * The caller's organizations for specs that mock every `/api/**` route. Since org-authz
 * the shell resolves the active organization from `GET /api/me/organizations` (a list);
 * a catch-all object answer leaves the app without an organization and nothing renders.
 */
export const MOCK_ORGANIZATIONS = [
  {
    id: '00000000-0000-4000-8000-0000000000e1',
    name: '기본 조직',
    slug: 'default',
    tenant_id: '00000000-0000-4000-8000-0000000000e2',
    tenant_name: '기본 회사',
    tenant_status: 'active',
    tenant_kind: 'company',
    is_default: true,
    is_active: true,
    roles: ['admin'],
    capabilities: [],
    capabilities_enforced: false,
  },
]
