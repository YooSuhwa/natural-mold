import { QueryClientProvider } from '@tanstack/react-query'
import { NextIntlClientProvider } from 'next-intl'
import type { ReactElement } from 'react'
import { render as rtlRender } from '@testing-library/react'
import { createTestQueryClient, render, screen, userEvent, waitFor } from '../../test-utils'
import messages from '../../../messages/ko.json'
import { ApiError } from '@/lib/api/errors'
import { organizationQueryKeys } from '@/lib/query-keys/organizations'
import { OrgMembersContent } from '@/features/organizations/components/org-members-content'
import type { DirectoryPage, MemberView } from '@/lib/types/organization'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { makeOrgView, orgApiMock } from './org-admin-mocks'

vi.mock('@/lib/api/organizations', async () => (await import('./org-admin-mocks')).orgApiMock)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { meOrganizationsApi, orgAdminApi } = orgApiMock

function renderWithClient(ui: ReactElement) {
  const queryClient = createTestQueryClient()
  rtlRender(
    <NextIntlClientProvider locale="ko" messages={messages}>
      <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
    </NextIntlClientProvider>,
  )
  return { queryClient }
}

const PAGE: DirectoryPage<MemberView> = {
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
  next_cursor: 'cursor-2',
  has_more: true,
  updated_at: 'v7',
}

describe('OrgMembersContent', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    orgAdminApi.listMembers.mockResolvedValue(PAGE)
    orgAdminApi.get.mockResolvedValue(makeOrgView({ permissions: [] }))
  })

  it('lets admins suspend with the page version and page forward by cursor', async () => {
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['admin'] })])
    orgAdminApi.get.mockResolvedValue(makeOrgView())
    orgAdminApi.setMemberStatus.mockResolvedValue({ ...PAGE.items[0], status: 'suspended' })
    render(<OrgMembersContent />)

    await userEvent.click(await screen.findByRole('button', { name: '정지' }))
    expect(orgAdminApi.setMemberStatus).toHaveBeenCalledWith('org-1', 'u1', 'suspended', 'v7')

    await userEvent.click(screen.getByRole('button', { name: /다음/ }))
    await waitFor(() =>
      expect(orgAdminApi.listMembers).toHaveBeenLastCalledWith('org-1', { cursor: 'cursor-2' }),
    )
  })

  it('hides member actions from non-admins', async () => {
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['builder'] })])
    render(<OrgMembersContent />)
    expect(await screen.findByText('Kim')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '정지' })).not.toBeInTheDocument()
    expect(screen.queryByText('멤버 추가')).not.toBeInTheDocument()
  })

  it('grants member actions to company admins via server permissions', async () => {
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: [] })])
    orgAdminApi.get.mockResolvedValue(makeOrgView({ permissions: ['can_manage_members'] }))
    render(<OrgMembersContent />)
    expect(await screen.findByRole('button', { name: '정지' })).toBeInTheDocument()
  })

  it('maps invalid_cursor to a localized message', async () => {
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['builder'] })])
    orgAdminApi.listMembers.mockRejectedValue(new ApiError(422, 'invalid_cursor', 'bad'))
    render(<OrgMembersContent />)
    expect(
      await screen.findByText('목록 위치 정보가 올바르지 않습니다. 처음부터 다시 불러와 주세요.'),
    ).toBeInTheDocument()
  })

  it('resets the pager cursor when the active organization changes', async () => {
    meOrganizationsApi.list.mockResolvedValue([makeOrganization({ roles: ['builder'] })])
    const { queryClient } = renderWithClient(<OrgMembersContent />)
    await userEvent.click(await screen.findByRole('button', { name: /다음/ }))
    await waitFor(() =>
      expect(orgAdminApi.listMembers).toHaveBeenLastCalledWith('org-1', { cursor: 'cursor-2' }),
    )
    queryClient.setQueryData(organizationQueryKeys.mine, [
      makeOrganization({ is_active: false }),
      makeOrganization({ id: 'org-2', name: 'Beta', roles: ['builder'] }),
    ])
    await waitFor(() =>
      expect(orgAdminApi.listMembers).toHaveBeenLastCalledWith('org-2', { cursor: null }),
    )
  })
})
