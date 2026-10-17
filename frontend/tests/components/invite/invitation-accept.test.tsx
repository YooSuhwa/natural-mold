import { render, screen, userEvent, waitFor } from '../../test-utils'
import { InvitationAccept } from '@/app/invite/[token]/_components/invitation-accept'
import { ApiError } from '@/lib/api/errors'
import { proxy } from '@/proxy'
import { NextRequest } from 'next/server'

const mocks = vi.hoisted(() => ({
  session: vi.fn(),
  accept: vi.fn(),
  logout: vi.fn(),
}))

vi.mock('@/lib/auth/auth-page-session', () => ({ getAuthPageSession: () => mocks.session() }))
vi.mock('@/lib/api/invitations', () => ({ invitationsApi: { accept: mocks.accept } }))
vi.mock('@/lib/hooks/useAuth', () => ({
  useLogout: () => ({ mutate: mocks.logout, isPending: false }),
}))

const USER = { id: 'u1', email: 'me@x.io' }

describe('InvitationAccept (S12)', () => {
  const assign = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('location', { ...window.location, assign })
  })
  afterEach(() => vi.unstubAllGlobals())

  it('offers sign-up with the token and login that returns to the invite', async () => {
    mocks.session.mockResolvedValue(null)
    render(<InvitationAccept token="ov1.abc" />)
    expect(await screen.findByRole('link', { name: '가입하고 참여하기' })).toHaveAttribute(
      'href',
      '/register?invitation=ov1.abc',
    )
    expect(screen.getByRole('link', { name: /로그인 후 수락/ })).toHaveAttribute(
      'href',
      `/login?callbackUrl=${encodeURIComponent('/invite/ov1.abc')}`,
    )
    expect(mocks.accept).not.toHaveBeenCalled()
  })

  it('joins with one click when signed in and reloads into the organization', async () => {
    mocks.session.mockResolvedValue(USER)
    mocks.accept.mockResolvedValue({ org_id: 'org-1', tenant_id: 't1', user_id: 'u1' })
    render(<InvitationAccept token="ov1.abc" />)
    expect(await screen.findByText('me@x.io 계정으로 참여합니다.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '참여하기' }))
    expect(mocks.accept).toHaveBeenCalledWith('ov1.abc')
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/'))
  })

  it('explains a wrong account and offers switching accounts', async () => {
    mocks.session.mockResolvedValue(USER)
    mocks.accept.mockRejectedValue(new ApiError(403, 'invitation_email_mismatch', 'x'))
    render(<InvitationAccept token="ov1.abc" />)
    await userEvent.click(await screen.findByRole('button', { name: '참여하기' }))
    expect(await screen.findByText('다른 계정으로 로그인돼 있어요')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '다른 계정으로 로그인' }))
    expect(mocks.logout).toHaveBeenCalled()
    expect(assign).not.toHaveBeenCalled()
  })

  it.each([
    ['invitation_expired', 410, '초대 링크가 만료됐어요'],
    ['tenant_invitation_revoked', 404, '사용할 수 없는 초대 링크예요'],
    ['invitation_invalid', 404, '사용할 수 없는 초대 링크예요'],
    ['invitation_used', 409, '이미 사용한 초대 링크예요'],
    ['invitation_membership_blocked', 409, '이 조직에 참여할 수 없어요'],
  ])('shows the %s state', async (code, status, title) => {
    mocks.session.mockResolvedValue(USER)
    mocks.accept.mockRejectedValue(new ApiError(status, code, 'x'))
    render(<InvitationAccept token="ov1.abc" />)
    await userEvent.click(await screen.findByRole('button', { name: '참여하기' }))
    expect(await screen.findByText(title)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '홈으로' })).toHaveAttribute('href', '/')
  })

  it('falls back to the shared error state for other failures', async () => {
    mocks.session.mockResolvedValue(USER)
    mocks.accept.mockRejectedValue(new ApiError(503, 'authz_unavailable', 'x'))
    render(<InvitationAccept token="ov1.abc" />)
    await userEvent.click(await screen.findByRole('button', { name: '참여하기' }))
    expect(await screen.findByRole('alert')).toHaveAttribute('data-error-kind', 'authz_unavailable')
  })

  it('is a public route in the proxy', () => {
    const response = proxy(new NextRequest('http://localhost/invite/ov1.abc'))
    expect(response.headers.get('location')).toBeNull()
  })
})
