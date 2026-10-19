import UsagePage from '@/app/settings/usage/page'
import AuditSettingsPage from '@/app/settings/audit/page'
import AdminAuditSettingsPage from '@/app/settings/admin-audit/page'
import { render, screen, waitFor } from '../../test-utils'

// Plan 13.9 row 12: /usage, /settings/usage, /settings/audit stay personal for every
// member; organization scope lives on the can_view_usage / can_view_audit screens.
const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  auditFilters: vi.fn(),
  session: vi.fn(),
  replace: vi.fn(),
}))

vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiFetch: (path: string) => mocks.apiFetch(path),
}))
vi.mock('@/lib/hooks/use-audit-events', () => ({
  useAuditEvents: (filters: unknown) => {
    mocks.auditFilters(filters)
    return { data: { pages: [] }, isLoading: false, isError: false, hasNextPage: false }
  },
}))
vi.mock('@/lib/auth/session', () => ({ useSession: () => mocks.session() }))
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: mocks.replace, push: vi.fn() }),
  usePathname: () => '/settings',
}))
vi.mock('@/components/usage/spend-line-chart', () => ({ SpendLineChart: () => null }))
vi.mock('@/components/usage/spend-bar-chart', () => ({ SpendBarChart: () => null }))

const member = { id: 'u-1', is_super_user: false }

describe('personal usage and audit scope', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.apiFetch.mockResolvedValue([])
    mocks.session.mockReturnValue({ data: member, isPending: false })
  })

  it('usage asks only the personal endpoints, whatever the caller can do in the org', async () => {
    render(<UsagePage />)
    await waitFor(() => expect(mocks.apiFetch).toHaveBeenCalled())
    const paths = mocks.apiFetch.mock.calls.map(([path]) => String(path))
    expect(paths.every((path) => path.startsWith('/api/usage/'))).toBe(true)
    expect(paths.some((path) => path.includes('/api/orgs/'))).toBe(false)
  })

  it('a member without can_view_audit sees only their own activity', () => {
    render(<AuditSettingsPage />)
    expect(mocks.auditFilters).toHaveBeenCalledWith(expect.objectContaining({ scope: 'mine' }))
  })

  it('admin audit sends a non-operator away without loading events', async () => {
    render(<AdminAuditSettingsPage />)
    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith('/'))
    expect(mocks.auditFilters).not.toHaveBeenCalled()
  })

  it('admin audit loads the full scope for the operator', () => {
    mocks.session.mockReturnValue({ data: { ...member, is_super_user: true }, isPending: false })
    render(<AdminAuditSettingsPage />)
    expect(mocks.auditFilters).toHaveBeenCalledWith(expect.objectContaining({ scope: 'all' }))
    expect(screen.queryByText(/권한이 필요해요/)).not.toBeInTheDocument()
  })
})
