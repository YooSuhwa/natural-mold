import { toast } from 'sonner'

import { PermissionRequiredState } from '@/components/access-requests/permission-required-state'
import { RequestAccessDialog } from '@/components/access-requests/request-access-dialog'
import { ApiError } from '@/lib/api/errors'
import { render, screen, userEvent, waitFor } from '../../test-utils'

const api = vi.hoisted(() => ({ create: vi.fn() }))
vi.mock('@/lib/api/access-requests', () => ({ accessRequestsApi: api }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

describe('RequestAccessDialog', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sends the chosen level and reason and treats the body-less 202 as sent', async () => {
    api.create.mockResolvedValue(undefined) // 202: nothing revealed (plan 8.4)
    const onOpenChange = vi.fn()
    render(
      <RequestAccessDialog
        open
        onOpenChange={onOpenChange}
        resourceType="skill"
        resourceId="skill-1"
        resourceName="영업 리포트"
      />,
    )
    expect(screen.getByText('영업 리포트 · 권한 요청')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('radio', { name: /보기/ }))
    await userEvent.type(screen.getByLabelText('필요한 이유 (선택)'), '검토용')
    await userEvent.click(screen.getByRole('button', { name: '요청 보내기' }))
    await waitFor(() =>
      expect(api.create).toHaveBeenCalledWith({
        resource_type: 'skill',
        resource_id: 'skill-1',
        level: 'viewer',
        reason: '검토용',
      }),
    )
    expect(toast.success).toHaveBeenCalledWith('요청을 보냈어요. 승인되면 바로 사용할 수 있어요.')
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('explains an already held level without echoing server text', async () => {
    api.create.mockRejectedValue(
      new ApiError(422, 'grant_invalid', 'raw', { reason: 'already_granted' }),
    )
    render(<RequestAccessDialog open onOpenChange={vi.fn()} resourceType="tool" resourceId="t-1" />)
    expect(screen.getByText('권한 요청', { selector: 'h2' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '요청 보내기' }))
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('이미 이 권한이 있어요. 새로고침해 보세요.'),
    )
  })
})

describe('PermissionRequiredState', () => {
  it('opens the request dialog for a requestable 403', async () => {
    const error = new ApiError(403, 'permission_required', 'x', {
      required: 'can_view',
      request_access: true,
    })
    render(<PermissionRequiredState error={error} resourceType="skill" resourceId="skill-1" />)
    expect(screen.getByText('권한이 필요해요')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '권한 요청' }))
    expect(await screen.findByRole('button', { name: '요청 보내기' })).toBeInTheDocument()
  })

  it('hides the button when the server says no request is possible', () => {
    const error = new ApiError(403, 'permission_required', 'x', { request_access: false })
    render(<PermissionRequiredState error={error} resourceType="skill" resourceId="skill-1" />)
    expect(screen.queryByRole('button', { name: '권한 요청' })).not.toBeInTheDocument()
    expect(
      screen.getByText('이 화면을 볼 권한이 없어요. 소유자에게 문의하세요.'),
    ).toBeInTheDocument()
  })
})
