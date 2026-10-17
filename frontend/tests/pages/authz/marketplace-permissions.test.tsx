import { Suspense } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { render, screen } from '../../test-utils'
import MarketplaceItemDetailPage from '@/app/marketplace/[item-id]/page'
import MarketplaceCatalogPage from '@/app/marketplace/page'
import { MarketplaceCard } from '@/components/marketplace/marketplace-card'
import { useCanPublishMarketplace } from '@/components/marketplace/use-marketplace-publish'
import { ApiError } from '@/lib/api/errors'
import type { MarketplaceItem } from '@/lib/types/marketplace'
import type { OrganizationSummary } from '@/lib/types/organization'

const state = vi.hoisted(() => ({
  item: { data: null as unknown, isLoading: false, error: null as unknown },
  items: [] as unknown[],
  orgs: [] as unknown[],
  permissions: vi.fn(),
}))

vi.mock('next/link', () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}))
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/',
}))
vi.mock('@/components/marketplace/install-wizard', () => ({ InstallWizard: () => null }))
vi.mock('@/components/marketplace/update-strategy-dialog', () => ({
  UpdateStrategyDialog: () => null,
}))
vi.mock('@/components/marketplace/marketplace-share-dialog', () => ({
  MarketplaceShareDialog: () => null,
}))
vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({ data: { id: 'user-1', is_super_user: false } }),
}))
vi.mock('@/lib/hooks/use-marketplace', () => ({
  useMarketplaceItem: () => state.item,
  useMarketplaceItemsPage: () => ({
    data: { items: state.items, total: state.items.length },
    isLoading: false,
  }),
  useMarketplaceVersions: () => ({ data: [] }),
  useDisableItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useEnableItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePatchMarketplaceItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/api/resource-permissions', () => ({
  RESOURCE_PERMISSION_BATCH: 100,
  resourcePermissionsApi: { list: state.permissions },
}))
vi.mock('@/lib/api/organizations', () => ({
  meOrganizationsApi: { list: vi.fn(async () => state.orgs) },
}))

/** The permissions route answers `held` for item-1 (`null` = item left out). */
function answer(held: string[] | null) {
  state.permissions.mockResolvedValue({
    items: held ? [{ id: 'item-1', my_permissions: held }] : [],
  })
}

function makeItem(overrides: Partial<MarketplaceItem> = {}): MarketplaceItem {
  return {
    id: 'item-1',
    resource_type: 'skill',
    name: '이미지 생성',
    slug: 'image-generation',
    description: null,
    visibility: 'public',
    status: 'published',
    is_system: false,
    is_listed: true,
    created_at: '2026-05-01T00:00:00Z',
    updated_at: '2026-05-02T00:00:00Z',
    credential_summary: {
      status: 'none',
      required_count: 0,
      optional_count: 0,
      missing_required_count: 0,
    },
    publication_summary: { state: 'not_published', is_listed: true, shared_user_count: 0 },
    installation: { installed: false, update_available: false, dirty: false },
    ...overrides,
  } as MarketplaceItem
}

async function renderDetail(item: MarketplaceItem | null, error: unknown = null) {
  state.item = { data: item, isLoading: false, error }
  await act(async () => {
    render(
      <Suspense fallback={null}>
        <MarketplaceItemDetailPage params={Promise.resolve({ 'item-id': 'item-1' })} />
      </Suspense>,
    )
    await Promise.resolve()
  })
}

const install = { name: '설치' }
const RETRY = '다시 시도'

describe('marketplace detail by permission (plan 13.9 마켓플레이스)', () => {
  beforeEach(() => {
    state.permissions.mockReset()
  })

  it('asks the marketplace_item permissions route for the item', async () => {
    answer(['can_view'])
    await renderDetail(makeItem())
    await waitFor(() =>
      expect(state.permissions).toHaveBeenCalledWith('marketplace_item', ['item-1']),
    )
  })

  it('viewer (can_view): details only, no install, no management', async () => {
    answer(['can_view'])
    await renderDetail(makeItem())
    expect(await screen.findByText('이미지 생성')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', install)).toBeNull())
    expect(screen.queryByText('소유자 관리')).toBeNull()
  })

  it('installer (can_install): install, no management', async () => {
    answer(['can_view', 'can_install'])
    await renderDetail(makeItem())
    expect(await screen.findByRole('button', install)).toBeInTheDocument()
    expect(screen.queryByText('소유자 관리')).toBeNull()
  })

  it('manager (can_manage): management controls', async () => {
    answer(['can_view', 'can_install', 'can_manage'])
    await renderDetail(makeItem())
    expect(await screen.findByText('소유자 관리')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '비공개로 전환' })).toBeInTheDocument()
  })

  it('until the answer arrives: management hidden (fail closed)', async () => {
    state.permissions.mockReturnValue(new Promise(() => {}))
    await renderDetail(makeItem())
    expect(await screen.findByText('이미지 생성')).toBeInTheDocument()
    expect(screen.queryByText('소유자 관리')).toBeNull()
  })

  it('answer failed (503): shared error state with retry, management hidden', async () => {
    state.permissions.mockRejectedValue(new ApiError(503, 'authz_unavailable', 'x'))
    await renderDetail(makeItem())
    const retry = await screen.findByRole('button', { name: RETRY })
    expect(screen.getByText('이미지 생성')).toBeInTheDocument()
    expect(screen.queryByText('소유자 관리')).toBeNull()
    answer(['can_view', 'can_install', 'can_manage'])
    await act(async () => retry.click())
    expect(await screen.findByText('소유자 관리')).toBeInTheDocument()
  })

  it('item left out of the answer: neither install nor management', async () => {
    answer(null)
    await renderDetail(makeItem())
    await waitFor(() => expect(screen.queryByRole('button', install)).toBeNull())
    expect(screen.queryByText('소유자 관리')).toBeNull()
  })

  it('none (hidden item): the shared not-found state', async () => {
    await renderDetail(null, new ApiError(404, 'MARKETPLACE_ITEM_NOT_FOUND', 'x'))
    expect(await screen.findByText('찾을 수 없어요')).toBeInTheDocument()
    expect(state.permissions).not.toHaveBeenCalled()
  })

  it('cards send a view-only grant to the details instead of installing', () => {
    render(<MarketplaceCard item={makeItem()} canInstall={false} />)
    expect(screen.queryByRole('button', install)).toBeNull()
    expect(screen.getAllByText('상세 보기').length).toBeGreaterThan(0)
  })

  it('catalog cards offer install with can_install', async () => {
    state.items = [makeItem()]
    answer(['can_view', 'can_install'])
    render(<MarketplaceCatalogPage />)
    expect(await screen.findByRole('button', install)).toBeInTheDocument()
  })

  it('catalog cards hide install for a view-only answer', async () => {
    state.items = [makeItem()]
    answer(['can_view'])
    render(<MarketplaceCatalogPage />)
    await waitFor(() =>
      expect(state.permissions).toHaveBeenCalledWith('marketplace_item', ['item-1']),
    )
    await waitFor(() => expect(screen.queryByRole('button', install)).toBeNull())
    expect(screen.getAllByText('상세 보기').length).toBeGreaterThan(0)
  })
})

function org(overrides: Partial<OrganizationSummary>): OrganizationSummary {
  return {
    id: 'org-1',
    name: 'Org',
    slug: 'org',
    tenant_id: 't',
    tenant_name: 'T',
    tenant_status: 'active',
    is_default: true,
    is_active: true,
    roles: [],
    capabilities: [],
    ...overrides,
  }
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('publishing needs cap_publish_marketplace', () => {
  it.each([
    [[org({ capabilities_enforced: true })], false],
    [[org({ capabilities_enforced: true, capabilities: ['cap_publish_marketplace'] })], true],
    [[org({ capabilities_enforced: false })], true],
  ])('orgs %j -> %s', async (orgs, expected) => {
    state.orgs = orgs
    const { result } = renderHook(() => useCanPublishMarketplace(), { wrapper })
    await waitFor(() => expect(result.current).toBe(expected))
  })
})
