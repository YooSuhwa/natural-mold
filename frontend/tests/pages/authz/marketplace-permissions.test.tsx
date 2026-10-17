import { Suspense } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { render, screen } from '../../test-utils'
import MarketplaceItemDetailPage from '@/app/marketplace/[item-id]/page'
import { MarketplaceCard } from '@/components/marketplace/marketplace-card'
import { useCanPublishMarketplace } from '@/components/marketplace/use-marketplace-publish'
import { ApiError } from '@/lib/api/errors'
import type { MarketplaceItemWithPermissions } from '@/components/marketplace/marketplace-access'
import type { MarketplaceItem } from '@/lib/types/marketplace'
import type { OrganizationSummary } from '@/lib/types/organization'

const state = vi.hoisted(() => ({
  item: { data: null as unknown, isLoading: false, error: null as unknown },
  orgs: [] as unknown[],
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
  useMarketplaceVersions: () => ({ data: [] }),
  useDisableItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useEnableItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePatchMarketplaceItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/api/organizations', () => ({
  meOrganizationsApi: { list: vi.fn(async () => state.orgs) },
}))

function makeItem(
  overrides: Partial<MarketplaceItemWithPermissions> = {},
): MarketplaceItemWithPermissions {
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
  } as MarketplaceItemWithPermissions
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

describe('marketplace detail by permission (plan 13.9 마켓플레이스)', () => {
  it('viewer (can_view): details only, no install, no management', async () => {
    await renderDetail(makeItem({ my_permissions: ['can_view'] }))
    expect(await screen.findByText('이미지 생성')).toBeInTheDocument()
    expect(screen.queryByRole('button', install)).toBeNull()
    expect(screen.queryByText('소유자 관리')).toBeNull()
  })

  it('installer (can_install): install, no management', async () => {
    await renderDetail(makeItem({ my_permissions: ['can_view', 'can_install'] }))
    expect(await screen.findByRole('button', install)).toBeInTheDocument()
    expect(screen.queryByText('소유자 관리')).toBeNull()
  })

  it('manager (can_manage): management controls', async () => {
    await renderDetail(makeItem({ my_permissions: ['can_view', 'can_install', 'can_manage'] }))
    expect(await screen.findByText('소유자 관리')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '비공개로 전환' })).toBeInTheDocument()
  })

  it('owner in legacy mode (no my_permissions): management controls', async () => {
    await renderDetail(
      makeItem({
        publication_summary: {
          state: 'published',
          is_listed: true,
          shared_user_count: 0,
          item_id: 'item-1',
        } as MarketplaceItem['publication_summary'],
      }),
    )
    expect(await screen.findByText('소유자 관리')).toBeInTheDocument()
  })

  it('none (hidden item): the shared not-found state', async () => {
    await renderDetail(null, new ApiError(404, 'MARKETPLACE_ITEM_NOT_FOUND', 'x'))
    expect(await screen.findByText('찾을 수 없어요')).toBeInTheDocument()
  })

  it('cards send a view-only grant to the details instead of installing', () => {
    render(<MarketplaceCard item={makeItem({ my_permissions: ['can_view'] })} />)
    expect(screen.queryByRole('button', install)).toBeNull()
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
