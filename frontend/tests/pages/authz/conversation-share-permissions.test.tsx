import { toast } from 'sonner'

import { ShareDialog } from '@/components/chat/share-dialog'
import { ApiError } from '@/lib/api/errors'
import { render, screen, userEvent, waitFor } from '../../test-utils'

// Plan 13.9 row 14: conversation links stay separate from resource sharing; when the
// organization turns public links off, creating one is disabled (old links 404 server-side).
const mocks = vi.hoisted(() => ({ active: vi.fn(), create: vi.fn(), org: vi.fn() }))

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/lib/hooks/use-share', () => ({
  useActiveShare: () => mocks.active(),
  useCreateShare: () => ({ mutateAsync: mocks.create, isPending: false }),
  useRevokeShare: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-organizations', () => ({
  useActiveOrganization: () => ({ activeOrganization: { id: 'org-1' } }),
  useOrganizationInfo: () => mocks.org(),
}))

const DISABLED = '이 조직은 공개 공유 링크를 허용하지 않아요. 조직 관리자에게 문의하세요.'

function orgAllows(allow: boolean | undefined) {
  mocks.org.mockReturnValue({
    data: allow === undefined ? undefined : { settings: { allow_public_share_links: allow } },
  })
}

describe('conversation share link', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.active.mockReturnValue({ data: null, isLoading: false })
  })

  it('organization allows public links: the owner creates one', async () => {
    orgAllows(true)
    mocks.create.mockResolvedValue({ share_token: 't' })
    render(<ShareDialog open onOpenChange={vi.fn()} conversationId="c-1" />)
    await userEvent.click(screen.getByRole('button', { name: '공유 링크 만들기' }))
    expect(mocks.create).toHaveBeenCalled()
    expect(screen.queryByText(DISABLED)).not.toBeInTheDocument()
  })

  it('organization disallows public links: creation is disabled with the reason', () => {
    orgAllows(false)
    render(<ShareDialog open onOpenChange={vi.fn()} conversationId="c-1" />)
    expect(screen.getByRole('button', { name: '공유 링크 만들기' })).toBeDisabled()
    expect(screen.getByText(DISABLED)).toBeInTheDocument()
  })

  it('a refusal by the server is explained, and outages use the shared message', async () => {
    orgAllows(undefined) // org info not loaded: the server decides
    mocks.create.mockRejectedValueOnce(new ApiError(403, 'PUBLIC_SHARE_LINKS_DISABLED', 'raw'))
    render(<ShareDialog open onOpenChange={vi.fn()} conversationId="c-1" />)
    const create = screen.getByRole('button', { name: '공유 링크 만들기' })
    await userEvent.click(create)
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(DISABLED))
    mocks.create.mockRejectedValueOnce(new ApiError(503, 'authz_unavailable', 'raw'))
    await userEvent.click(create)
    await waitFor(() =>
      expect(toast.error).toHaveBeenLastCalledWith(
        '권한 서버에 일시적인 문제가 있어요. 잠시 후 다시 시도하세요.',
      ),
    )
  })

  it('an existing link can still be revoked while links are off', () => {
    orgAllows(false)
    mocks.active.mockReturnValue({ data: { share_token: 'tok' }, isLoading: false })
    render(<ShareDialog open onOpenChange={vi.fn()} conversationId="c-1" />)
    expect(screen.getByRole('button', { name: '공유 해제' })).toBeEnabled()
  })
})
