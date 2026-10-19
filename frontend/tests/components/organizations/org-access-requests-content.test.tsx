import { toast } from 'sonner'

import { OrgAccessRequestsContent } from '@/features/organizations/components/org-access-requests-content'
import { ApiError } from '@/lib/api/errors'
import { render, screen, userEvent, waitFor } from '../../test-utils'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { makeAccessRequest } from '../access-requests/access-request-fixtures'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  approve: vi.fn(),
  deny: vi.fn(),
  cancel: vi.fn(),
}))
const orgs = vi.hoisted(() => ({ active: vi.fn() }))

vi.mock('@/lib/api/access-requests', () => ({ accessRequestsApi: api }))
vi.mock('@/lib/hooks/use-organizations', () => ({ useActiveOrganization: () => orgs.active() }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const page = (items = [makeAccessRequest()]) => ({ items, next_cursor: null, has_more: false })

describe('OrgAccessRequestsContent', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    orgs.active.mockReturnValue({ activeOrganization: makeOrganization(), isLoading: false })
    api.list.mockResolvedValue(page())
  })

  it('lists received requests and approves one as requested', async () => {
    api.approve.mockResolvedValue(makeAccessRequest({ status: 'approved' }))
    render(<OrgAccessRequestsContent />)
    expect(await screen.findByText('정우진 → 영업 리포트 · 편집')).toBeInTheDocument()
    expect(screen.getByText('30일 후 만료')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '승인' }))
    await waitFor(() => expect(api.approve).toHaveBeenCalledWith('req-1', undefined))
    expect(api.list).toHaveBeenCalledWith('inbox', null)
    expect(toast.success).toHaveBeenCalledWith('요청을 승인했어요')
  })

  it('maps an already handled request to localized copy', async () => {
    api.deny.mockRejectedValue(new ApiError(409, 'access_request_closed', 'raw'))
    render(<OrgAccessRequestsContent />)
    await userEvent.click(await screen.findByRole('button', { name: '거절' }))
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('이미 처리된 요청이에요. 목록을 새로고침했어요.'),
    )
  })

  it('switches to sent requests where the requester can cancel', async () => {
    api.cancel.mockResolvedValue(makeAccessRequest({ status: 'cancelled' }))
    render(<OrgAccessRequestsContent />)
    await userEvent.click(await screen.findByRole('tab', { name: '보낸 요청' }))
    await waitFor(() => expect(api.list).toHaveBeenCalledWith('outbox', null))
    await userEvent.click(await screen.findByRole('button', { name: '요청 취소' }))
    await waitFor(() => expect(api.cancel).toHaveBeenCalledWith('req-1'))
  })

  it('shows the empty state of the box', async () => {
    api.list.mockResolvedValue(page([]))
    render(<OrgAccessRequestsContent />)
    expect(await screen.findByText('받은 요청이 없어요')).toBeInTheDocument()
  })
})
