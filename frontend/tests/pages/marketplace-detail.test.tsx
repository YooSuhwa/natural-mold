import { Suspense } from 'react'
import { act } from '@testing-library/react'
import { render, screen, userEvent, waitFor } from '../test-utils'
import MarketplaceItemDetailPage from '@/app/marketplace/[item-id]/page'
import type { MarketplaceItem, MarketplaceVersionSummary } from '@/lib/types/marketplace'

const mockPush = vi.hoisted(() => vi.fn())

vi.mock('next/link', () => ({
  default: ({
    children,
    href,
    ...props
  }: {
    children: React.ReactNode
    href: string
    [key: string]: unknown
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: mockPush,
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
  }),
  usePathname: () => '/',
  useParams: () => ({}),
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock('@/components/marketplace/install-wizard', () => ({
  InstallWizard: () => null,
}))

vi.mock('@/components/marketplace/update-strategy-dialog', () => ({
  UpdateStrategyDialog: () => null,
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({ data: { id: 'user-1', is_super_user: false } }),
}))

const mockUseMarketplaceItem = vi.fn()
const mockUseMarketplaceVersions = vi.fn()

vi.mock('@/lib/hooks/use-marketplace', () => ({
  useMarketplaceItem: () => mockUseMarketplaceItem(),
  useMarketplaceVersions: () => mockUseMarketplaceVersions(),
  useDisableItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useEnableItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePatchMarketplaceItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRemoveItemACL: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

const versions: MarketplaceVersionSummary[] = [
  {
    id: 'version-2',
    version_label: '1.0.0',
    version_number: 2,
    content_hash: 'abcdef1234567890',
    created_at: '2026-05-02T00:00:00Z',
  },
  {
    id: 'version-1',
    version_label: '1.0.0',
    version_number: 1,
    content_hash: '1234567890abcdef',
    created_at: '2026-05-01T00:00:00Z',
  },
]

const item: MarketplaceItem = {
  id: 'item-1',
  resource_type: 'skill',
  name: '이미지 생성',
  slug: 'image-generation',
  description: '이미지를 생성합니다.',
  visibility: 'public',
  status: 'published',
  is_system: false,
  is_listed: true,
  tags: [],
  categories: [],
  locale: 'ko-KR',
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-02T00:00:00Z',
  latest_version: versions[0],
  credential_summary: {
    status: 'none',
    required_count: 0,
    optional_count: 0,
    missing_required_count: 0,
  },
  execution_profile: {
    support_level: 'proxy_http',
    runners: ['python'],
    requires_network: true,
    notes: '프록시를 사용합니다.',
  },
  origin_summary: null,
  publication_summary: {
    state: 'not_published',
    is_listed: true,
    shared_user_count: 0,
  },
  installation: {
    installed: false,
    update_available: false,
    dirty: false,
  },
}

describe('MarketplaceItemDetailPage', () => {
  beforeEach(() => {
    mockPush.mockClear()
    mockUseMarketplaceItem.mockReturnValue({ data: item, isLoading: false, error: null })
    mockUseMarketplaceVersions.mockReturnValue({ data: versions })
  })

  async function renderDetailPage() {
    await act(async () => {
      render(
        <Suspense fallback={null}>
          <MarketplaceItemDetailPage params={Promise.resolve({ 'item-id': 'item-1' })} />
        </Suspense>,
      )
      await Promise.resolve()
    })
  }

  it('distinguishes duplicate version labels with version number, latest badge, and hash', async () => {
    await renderDetailPage()

    expect(await screen.findByText('#2')).toBeInTheDocument()
    expect(screen.getByText('최신')).toBeInTheDocument()
    expect(screen.getByText('abcdef1')).toBeInTheDocument()
    expect(screen.getByText('#1')).toBeInTheDocument()
    expect(screen.getByText('1234567')).toBeInTheDocument()
  })

  it('renders execution profile with Korean labels instead of raw keys', async () => {
    await renderDetailPage()

    expect(await screen.findByText('지원 방식')).toBeInTheDocument()
    expect(screen.getAllByText('프록시 필요').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('실행기')).toBeInTheDocument()
    expect(screen.getByText('python')).toBeInTheDocument()
    expect(screen.getByText('네트워크')).toBeInTheDocument()
    expect(screen.getByText('필요')).toBeInTheDocument()
    expect(screen.queryByText('support_level')).not.toBeInTheDocument()
  })

  it.each([
    {
      resource_type: 'mcp' as const,
      installed_resource_id: 'mcp-server-1',
      expected_href: '/mcp-servers?detailId=mcp-server-1',
    },
    {
      resource_type: 'agent' as const,
      installed_resource_id: 'blueprint-1',
      expected_href: '/agents/new/template?blueprintId=blueprint-1',
    },
  ])(
    'routes installed $resource_type marketplace detail Open CTA to its resource screen',
    async ({ resource_type, installed_resource_id, expected_href }) => {
      mockUseMarketplaceItem.mockReturnValue({
        data: {
          ...item,
          resource_type,
          installation: {
            installed: true,
            installation_id: 'installation-1',
            installed_resource_id,
            status: 'active',
            update_available: false,
            dirty: false,
          },
        } satisfies MarketplaceItem,
        isLoading: false,
        error: null,
      })

      await renderDetailPage()
      await userEvent.click(await screen.findByRole('button', { name: '열기' }))

      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith(expected_href)
      })
    },
  )
})
