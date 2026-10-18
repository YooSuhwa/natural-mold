import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { MarketplaceShareDialog } from '@/components/marketplace/marketplace-share-dialog'
import { ApiError } from '@/lib/api/errors'
import type { MarketplaceItem } from '@/lib/types/marketplace'
import { toast } from 'sonner'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { orgApiMock } from '../organizations/org-admin-mocks'
import { trapConsole } from './sharing-mocks'

const { marketplaceApi } = vi.hoisted(() => ({
  marketplaceApi: { replaceACL: vi.fn(), removeACLEntry: vi.fn(), listACL: vi.fn() },
}))

vi.mock('@/lib/api/marketplace', () => ({ marketplaceApi }))
vi.mock(
  '@/lib/api/organizations',
  async () => (await import('../organizations/org-admin-mocks')).orgApiMock,
)
vi.mock('@/lib/auth/session', () => ({ useSession: () => ({ data: { id: 'user-me' } }) }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { meOrganizationsApi } = orgApiMock

function makeItem(aclUserIds: string[]): MarketplaceItem {
  return {
    id: 'item-1',
    name: 'Image skill',
    visibility: 'restricted',
    acl_user_ids: aclUserIds,
  } as unknown as MarketplaceItem
}

describe('MarketplaceShareDialog (revoke-only ACL adapter until I-04)', () => {
  let consoleTrap: ReturnType<typeof trapConsole>

  beforeEach(() => {
    vi.clearAllMocks()
    consoleTrap = trapConsole()
    meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
    marketplaceApi.listACL.mockResolvedValue({ items: [] })
  })

  afterEach(() => {
    consoleTrap.assertClean()
    consoleTrap.restore()
  })

  it('lists recipients without an add form, a level choice or transfer', async () => {
    render(<MarketplaceShareDialog item={makeItem(['user-a'])} open onOpenChange={vi.fn()} />)
    expect(await screen.findByRole('dialog', { name: 'Image skill 공유' })).toBeInTheDocument()
    expect(await screen.findByRole('note')).toHaveTextContent('지금은 회수만 할 수 있고')
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '공유' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '소유권 이전' })).not.toBeInTheDocument()
    expect(screen.queryByText(/공유 권한이 없어/)).not.toBeInTheDocument()
    expect(marketplaceApi.replaceACL).not.toHaveBeenCalled()
  })

  it('names recipients by display name instead of their user id', async () => {
    marketplaceApi.listACL.mockResolvedValue({
      items: [{ user_id: 'user-a', display_name: '이하은', permission: 'install' }],
    })
    render(<MarketplaceShareDialog item={makeItem(['user-a'])} open onOpenChange={vi.fn()} />)
    expect(await screen.findByText('이하은')).toBeInTheDocument()
    expect(screen.queryByText('user-a')).not.toBeInTheDocument()
    expect(marketplaceApi.listACL).toHaveBeenCalledWith('item-1')
  })

  it('removes one recipient through the per-row ACL endpoint', async () => {
    marketplaceApi.removeACLEntry.mockResolvedValue(undefined)
    render(
      <MarketplaceShareDialog item={makeItem(['user-a', 'user-b'])} open onOpenChange={vi.fn()} />,
    )
    await userEvent.click(await screen.findByRole('button', { name: 'user-b 공유 회수' }))
    const alert = await screen.findByRole('alertdialog')
    await userEvent.click(within(alert).getByRole('button', { name: '회수' }))
    await waitFor(() =>
      expect(marketplaceApi.removeACLEntry).toHaveBeenCalledWith('item-1', 'user-b'),
    )
    expect(marketplaceApi.replaceACL).not.toHaveBeenCalled()
  })

  it('maps the last-target error', async () => {
    marketplaceApi.removeACLEntry.mockRejectedValue(
      new ApiError(409, 'marketplace_acl_required', 'last'),
    )
    render(<MarketplaceShareDialog item={makeItem(['user-a'])} open onOpenChange={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'user-a 공유 회수' }))
    const alert = await screen.findByRole('alertdialog')
    await userEvent.click(within(alert).getByRole('button', { name: '회수' }))
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        '제한 공개 항목에는 대상이 한 명 이상 있어야 해요. 공개 범위를 먼저 바꾸세요.',
      ),
    )
  })
})
