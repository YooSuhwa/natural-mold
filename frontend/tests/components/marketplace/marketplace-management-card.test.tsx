import { MarketplaceManagementCard } from '@/app/marketplace/[item-id]/_components/marketplace-management-card'
import type { MarketplaceItem } from '@/lib/types/marketplace'
import { render, screen } from '../../test-utils'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/lib/hooks/use-marketplace', () => ({
  useDisableItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useEnableItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePatchMarketplaceItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

describe('MarketplaceManagementCard visibility (capture 33)', () => {
  it('shows the visibility label, not the raw value', () => {
    const item = {
      id: 'item-1',
      visibility: 'restricted',
      status: 'published',
      acl_user_ids: ['u1', 'u2'],
    } as unknown as MarketplaceItem
    render(
      <MarketplaceManagementCard item={item} canManage isSuperUser={false} onManageAcl={vi.fn()} />,
    )
    expect(screen.getByText('제한 공유 (지정 사용자)')).toBeInTheDocument()
    expect(screen.queryByText('restricted')).not.toBeInTheDocument()
  })
})
