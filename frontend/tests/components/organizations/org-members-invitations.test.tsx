import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { OrgMembersContent } from '@/features/organizations/components/org-members-content'
import { parseInviteEmails } from '@/features/organizations/lib/invite-emails'
import { ApiError } from '@/lib/api/errors'
import type { InvitationView } from '@/lib/types/org-governance'
import type { DirectoryPage, MemberView } from '@/lib/types/organization'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import {
  governanceApiMock,
  makeLimitView,
  makeOrgView,
  orgApiMock,
  resetGovernanceMocks,
} from './org-admin-mocks'

vi.mock('@/lib/api/organizations', async () => (await import('./org-admin-mocks')).orgApiMock)
vi.mock(
  '@/lib/api/org-governance',
  async () => (await import('./org-admin-mocks')).governanceApiMock,
)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { meOrganizationsApi, orgAdminApi } = orgApiMock
const api = governanceApiMock.orgGovernanceApi

const MEMBERS: DirectoryPage<MemberView> = {
  items: [
    {
      user_id: 'u1',
      display_name: 'Kim',
      email: 'kim@example.com',
      status: 'active',
      joined_at: '2026-10-01T00:00:00Z',
      removed_at: null,
    },
  ],
  next_cursor: null,
  has_more: false,
  updated_at: 'v7',
}

function invitation(overrides: Partial<InvitationView> = {}): InvitationView {
  return {
    id: 'i1',
    email: 'new.hire@example.com',
    role: 'member',
    group_ids: [],
    expires_at: '2099-01-01T00:00:00Z',
    created_at: '2026-10-16T00:00:00Z',
    status: 'pending',
    ...overrides,
  }
}

describe('OrgMembersContent invitations and limits (S10/S11)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetGovernanceMocks()
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['admin'] })])
    orgAdminApi.get.mockResolvedValue(makeOrgView())
    orgAdminApi.listMembers.mockResolvedValue(MEMBERS)
    orgAdminApi.listGroups.mockResolvedValue({
      items: [],
      next_cursor: null,
      has_more: false,
      updated_at: 'v7',
    })
  })

  it('invites several emails with the default role and offers a copyable link', async () => {
    api.createInvitation.mockImplementation(async (_org: string, data: { email: string }) => ({
      ...invitation({ email: data.email }),
      token: data.email.startsWith('dup') ? null : 'ov1.secret',
    }))
    render(<OrgMembersContent />)

    await userEvent.click(await screen.findByRole('button', { name: '구성원 초대' }))
    const dialog = await screen.findByRole('dialog')
    const send = within(dialog).getByRole('button', { name: /초대 보내기/ })
    expect(send).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText(/이메일/), 'A@x.io, dup@x.io\na@x.io')
    expect(send).toHaveTextContent('초대 보내기 (2)')
    await userEvent.click(send)

    await waitFor(() => expect(api.createInvitation).toHaveBeenCalledTimes(2))
    expect(api.createInvitation).toHaveBeenCalledWith('org-1', {
      email: 'a@x.io',
      role: 'member',
      group_ids: [],
    })
    expect(await within(dialog).findByText('초대 링크를 만들었어요.')).toBeInTheDocument()
    expect(within(dialog).getByText(/이미 대기 중인 초대가 있어요/)).toBeInTheDocument()
    expect(
      within(dialog).getByRole('button', { name: 'a@x.io 초대 링크 복사' }),
    ).toBeInTheDocument()
    expect(dialog).not.toHaveTextContent('ov1.secret')
  })

  it('closes the invite dialog with Escape and returns focus to the trigger', async () => {
    render(<OrgMembersContent />)
    const trigger = await screen.findByRole('button', { name: '구성원 초대' })
    await userEvent.click(trigger)
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('rejects malformed emails before sending', async () => {
    render(<OrgMembersContent />)
    await userEvent.click(await screen.findByRole('button', { name: '구성원 초대' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText(/이메일/), 'ok@x.io, nope')
    expect(within(dialog).getByText('이메일 형식이 올바르지 않아요: nope')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: /초대 보내기/ })).toBeDisabled()
  })

  it('lists pending invitations and revokes after confirmation', async () => {
    api.listInvitations.mockResolvedValue({
      items: [invitation(), invitation({ id: 'i2', email: 'old@x.io', status: 'revoked' })],
      next_cursor: null,
      has_more: false,
      updated_at: 'v7',
    })
    api.revokeInvitation.mockResolvedValue(undefined)
    render(<OrgMembersContent />)

    expect(await screen.findByText('new.hire@example.com')).toBeInTheDocument()
    expect(screen.queryByText('old@x.io')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '초대 취소' }))
    const confirm = await screen.findByRole('alertdialog')
    await userEvent.click(within(confirm).getByRole('button', { name: '초대 취소' }))
    await waitFor(() => expect(api.revokeInvitation).toHaveBeenCalledWith('org-1', 'i1'))
  })

  it('resends an invitation and shows the new link to copy', async () => {
    api.listInvitations.mockResolvedValue({
      items: [invitation()],
      next_cursor: null,
      has_more: false,
      updated_at: 'v7',
    })
    api.resendInvitation.mockResolvedValue({ ...invitation({ id: 'i3' }), token: 'ov1.next' })
    render(<OrgMembersContent />)

    await userEvent.click(await screen.findByRole('button', { name: '재발송' }))
    const dialog = await screen.findByRole('dialog')
    expect(
      within(dialog).getByRole('button', { name: 'new.hire@example.com 초대 링크 복사' }),
    ).toBeInTheDocument()
    expect(api.resendInvitation).toHaveBeenCalledWith('org-1', 'i1')
  })

  it('sets a member monthly limit with the organization version', async () => {
    api.getUserLimit.mockResolvedValue(
      makeLimitView({ scope: 'user', id: 'u1', monthly_spend_limit: null, parent_limit: '100' }),
    )
    api.putUserLimit.mockResolvedValue(makeLimitView({ scope: 'user', id: 'u1' }))
    render(<OrgMembersContent />)

    await userEvent.click(await screen.findByRole('button', { name: '월 한도' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(await within(dialog).findByLabelText('월 한도(USD)'), '30')
    await userEvent.click(within(dialog).getByRole('button', { name: '저장' }))
    await waitFor(() => expect(api.putUserLimit).toHaveBeenCalledWith('org-1', 'u1', '30', 'v7'))
    expect(api.getUserLimit).toHaveBeenCalledWith('org-1', 'u1')
  })

  it('hides invitations and member admin actions from non-admins', async () => {
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['builder'] })])
    orgAdminApi.get.mockResolvedValue(makeOrgView({ permissions: [] }))
    render(<OrgMembersContent />)
    expect(await screen.findByText('Kim')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '구성원 초대' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '월 한도' })).not.toBeInTheDocument()
    expect(api.listInvitations).not.toHaveBeenCalled()
  })

  it('shows the shared error state when invitations fail to load', async () => {
    api.listInvitations.mockRejectedValue(new ApiError(503, 'authz_unavailable', 'down'))
    render(<OrgMembersContent />)
    expect(await screen.findByRole('alert')).toHaveAttribute('data-error-kind', 'authz_unavailable')
  })
})

describe('parseInviteEmails', () => {
  it('splits, lowercases and de-duplicates', () => {
    expect(parseInviteEmails(' A@x.io;b@y.io\n\na@x.io, bad ')).toEqual({
      valid: ['a@x.io', 'b@y.io'],
      invalid: ['bad'],
    })
  })
})
