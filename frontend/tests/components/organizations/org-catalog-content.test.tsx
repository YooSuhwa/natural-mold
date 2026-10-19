import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { OrgCatalogContent } from '@/features/organizations/components/org-catalog-content'
import { nextOverrides } from '@/features/organizations/lib/catalog-overrides'
import { ApiError } from '@/lib/api/errors'
import type { CatalogItemView } from '@/lib/types/org-governance'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { governanceApiMock, makeOrgView, orgApiMock } from './org-admin-mocks'

vi.mock('@/lib/api/organizations', async () => (await import('./org-admin-mocks')).orgApiMock)
vi.mock(
  '@/lib/api/org-governance',
  async () => (await import('./org-admin-mocks')).governanceApiMock,
)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { meOrganizationsApi, orgAdminApi } = orgApiMock
const api = governanceApiMock.orgGovernanceApi

function item(overrides: Partial<CatalogItemView>): CatalogItemView {
  return {
    type: 'model',
    id: 'm1',
    name: 'Claude Sonnet',
    source: 'platform',
    company_enabled: true,
    organization_enabled: true,
    enabled: true,
    ...overrides,
  }
}

const ITEMS = [
  item({}),
  item({
    id: 'm2',
    name: 'Qwen 72B',
    source: 'company',
    organization_enabled: false,
    enabled: false,
  }),
  item({ id: 'm3', name: 'Blocked', company_enabled: false, enabled: false }),
  item({ type: 'tool', id: 't1', name: 'Web search' }),
]

describe('OrgCatalogContent (S09)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['admin'] })])
    orgAdminApi.get.mockResolvedValue(makeOrgView())
    api.getCatalog.mockResolvedValue({ items: ITEMS, updated_at: 'v3' })
  })

  it('does not load the catalog without can_manage_policy (fail closed)', async () => {
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['builder'] })])
    orgAdminApi.get.mockResolvedValue(makeOrgView({ permissions: [] }))
    render(<OrgCatalogContent />)
    expect(await screen.findByText('조직 관리자만 볼 수 있습니다.')).toBeInTheDocument()
    expect(api.getCatalog).not.toHaveBeenCalled()
  })

  it('lists items per kind and locks company-disabled ones', async () => {
    render(<OrgCatalogContent />)
    expect(await screen.findByText('Claude Sonnet')).toBeInTheDocument()
    expect(screen.queryByText('Web search')).not.toBeInTheDocument()
    expect(screen.getByText('회사에서 꺼짐')).toBeInTheDocument()
    const switches = screen.getAllByRole('switch')
    expect(switches[2]).toHaveAttribute('aria-disabled', 'true')

    await userEvent.click(screen.getByRole('tab', { name: /시스템 도구/ }))
    expect(await screen.findByText('Web search')).toBeInTheDocument()
  })

  it('shows the impact before turning an item off and keeps other overrides', async () => {
    api.catalogImpact.mockResolvedValue({
      type: 'model',
      id: 'm1',
      affected_agents: 5,
      required_agents: 1,
    })
    api.putCatalog.mockResolvedValue({ items: ITEMS, updated_at: 'v4' })
    render(<OrgCatalogContent />)

    await screen.findByText('Claude Sonnet')
    await userEvent.click(screen.getAllByRole('switch')[0])
    const dialog = await screen.findByRole('dialog')
    expect(
      await within(dialog).findByText('에이전트 5개에서 이 항목이 빠집니다.'),
    ).toBeInTheDocument()
    expect(
      within(dialog).getByText('필수로 연결된 에이전트 1개는 실행할 수 없게 됩니다.'),
    ).toBeInTheDocument()
    expect(api.catalogImpact).toHaveBeenCalledWith('org-1', 'model', 'm1')
    expect(api.putCatalog).not.toHaveBeenCalled()

    await userEvent.click(within(dialog).getByRole('button', { name: '끄기' }))
    await waitFor(() =>
      expect(api.putCatalog).toHaveBeenCalledWith(
        'org-1',
        [
          { type: 'model', id: 'm1', enabled: false },
          { type: 'model', id: 'm2', enabled: false },
        ],
        'v3',
      ),
    )
  })

  it('turns an item back on without a confirmation', async () => {
    api.putCatalog.mockResolvedValue({ items: ITEMS, updated_at: 'v4' })
    render(<OrgCatalogContent />)
    await screen.findByText('Qwen 72B')
    await userEvent.click(screen.getAllByRole('switch')[1])
    await waitFor(() => expect(api.putCatalog).toHaveBeenCalledWith('org-1', [], 'v3'))
  })

  it('renders the shared error state when the catalog fails', async () => {
    api.getCatalog.mockRejectedValue(new ApiError(503, 'authz_unavailable', 'down'))
    render(<OrgCatalogContent />)
    expect(await screen.findByRole('alert')).toHaveAttribute('data-error-kind', 'authz_unavailable')
  })
})

describe('nextOverrides', () => {
  it('sends only explicit off entries', () => {
    expect(nextOverrides(ITEMS, ITEMS[1], true)).toEqual([])
    expect(nextOverrides(ITEMS, ITEMS[3], false)).toEqual([
      { type: 'model', id: 'm2', enabled: false },
      { type: 'tool', id: 't1', enabled: false },
    ])
  })
})

describe('OrgCatalogContent impact failure', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['admin'] })])
    orgAdminApi.get.mockResolvedValue(makeOrgView())
    api.getCatalog.mockResolvedValue({ items: ITEMS, updated_at: 'v3' })
  })

  it('keeps the off confirmation disabled when the impact preview fails (fail closed)', async () => {
    api.catalogImpact.mockRejectedValue(new ApiError(503, 'authz_unavailable', 'down'))
    render(<OrgCatalogContent />)
    await screen.findByText('Claude Sonnet')
    await userEvent.click(screen.getAllByRole('switch')[0])
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByRole('alert')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '끄기' })).toBeDisabled()
    expect(api.putCatalog).not.toHaveBeenCalled()
  })
})
