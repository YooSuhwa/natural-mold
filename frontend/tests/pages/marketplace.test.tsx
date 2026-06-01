import { render, screen, within } from '../test-utils'
import MarketplaceCatalogPage from '@/app/marketplace/page'
import type { MarketplaceItem } from '@/lib/types/marketplace'

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

vi.mock('@/components/marketplace/install-wizard', () => ({
  InstallWizard: () => null,
}))

vi.mock('@/components/marketplace/update-strategy-dialog', () => ({
  UpdateStrategyDialog: () => null,
}))

const mockUseMarketplaceItems = vi.fn()
const mockUseSession = vi.fn()

vi.mock('@/lib/hooks/use-marketplace', () => ({
  useMarketplaceItems: (...args: unknown[]) => mockUseMarketplaceItems(...args),
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: () => mockUseSession(),
}))

function marketplaceItem(overrides: Partial<MarketplaceItem> = {}): MarketplaceItem {
  return {
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
    latest_version: {
      id: 'version-1',
      version_label: '0.1.0',
      version_number: 1,
      content_hash: 'abc123',
      created_at: '2026-05-02T00:00:00Z',
    },
    credential_summary: {
      status: 'none',
      required_count: 0,
      optional_count: 0,
      missing_required_count: 0,
    },
    execution_profile: { support_level: 'ready_python' },
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
    ...overrides,
  }
}

describe('MarketplaceCatalogPage', () => {
  beforeEach(() => {
    mockUseSession.mockReturnValue({ data: { is_super_user: false } })
    mockUseMarketplaceItems.mockReturnValue({
      data: [
        marketplaceItem(),
        marketplaceItem({ id: 'item-2', name: '문서 요약', slug: 'document-summary' }),
      ],
      isLoading: false,
    })
  })

  it('uses the shared resource layout with one active tab count surface', () => {
    render(<MarketplaceCatalogPage />)

    const activeTab = screen.getByRole('tab', { name: '스킬 2개' })
    expect(activeTab).toHaveAttribute('aria-selected', 'true')
    expect(within(activeTab).getByText('2개')).toBeInTheDocument()
    expect(screen.getAllByText('2개')).toHaveLength(1)

    expect(screen.getByPlaceholderText('마켓플레이스 검색…')).toBeInTheDocument()
    expect(screen.getByText('이미지 생성')).toBeInTheDocument()
    expect(screen.getByText('문서 요약')).toBeInTheDocument()
  })
})
