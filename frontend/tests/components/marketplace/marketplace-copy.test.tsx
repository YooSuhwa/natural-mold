import { render, screen } from '../../test-utils'
import {
  derivePrimaryCta,
  MarketplaceCard,
} from '@/components/marketplace/marketplace-card'
import { MarketplaceFilterBar } from '@/components/marketplace/marketplace-filter-bar'
import { InstallationBadge } from '@/components/marketplace/badges/installation-badge'
import { SupportBadge } from '@/components/marketplace/badges/support-badge'
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

function item(overrides: Partial<MarketplaceItem> = {}): MarketplaceItem {
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

describe('marketplace Korean copy', () => {
  it('derives primary CTA kinds for translated labels', () => {
    expect(derivePrimaryCta(item()).kind).toBe('install')
    expect(
      derivePrimaryCta(
        item({
          installation: { installed: true, status: 'needs_setup', update_available: false, dirty: false },
        }),
      ).kind,
    ).toBe('setup')
    expect(
      derivePrimaryCta(
        item({
          installation: { installed: true, status: 'active', update_available: true, dirty: false },
        }),
      ).kind,
    ).toBe('update')
    expect(
      derivePrimaryCta(
        item({
          installation: { installed: true, status: 'active', update_available: false, dirty: false },
        }),
      ).kind,
    ).toBe('open')
  })

  it('renders Korean card CTA and marketplace badges', () => {
    render(
      <MarketplaceCard
        item={item({
          installation: {
            installed: true,
            status: 'active',
            update_available: true,
            dirty: true,
          },
          execution_profile: { support_level: 'proxy_http' },
        })}
      />,
    )

    expect(screen.getByRole('button', { name: '업데이트 검토' })).toBeInTheDocument()
    expect(screen.getByText('설치됨')).toBeInTheDocument()
    expect(screen.getByText('업데이트 가능')).toBeInTheDocument()
    expect(screen.getByText('수정됨')).toBeInTheDocument()
    expect(screen.getByText('프록시 필요')).toBeInTheDocument()
  })

  it('renders Korean filter placeholders and actions', () => {
    render(<MarketplaceFilterBar filters={{}} onChange={vi.fn()} superUser />)

    expect(screen.getByPlaceholderText('마켓플레이스 검색…')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '출처 필터' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '지원 방식 필터' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '설치 상태 필터' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '대기 항목 보기' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '초기화' })).toBeInTheDocument()
  })
})
