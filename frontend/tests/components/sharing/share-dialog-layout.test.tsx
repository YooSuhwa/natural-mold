import { render, screen, within } from '../../test-utils'
import { ResourceShareDialog } from '@/components/sharing/resource-share-dialog'
import { DIALOG_HEIGHT } from '@/lib/design-tokens'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { orgApiMock } from '../organizations/org-admin-mocks'
import { accessList, grantsApiMock } from './sharing-mocks'

vi.mock('@/lib/api/grants', async () => (await import('./sharing-mocks')).grantsApiMock)
vi.mock(
  '@/lib/api/organizations',
  async () => (await import('../organizations/org-admin-mocks')).orgApiMock,
)
vi.mock('@/lib/auth/session', () => ({ useSession: () => ({ data: { id: 'user-me' } }) }))

describe('ResourceShareDialog layout (S16 capture 31)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    orgApiMock.meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
    grantsApiMock.grantsApi.access.mockResolvedValue(accessList())
  })

  it('uses the fixed height token and keeps the submit row sticky in the scrolling body', async () => {
    render(
      <ResourceShareDialog
        open
        onOpenChange={vi.fn()}
        resourceType="agent"
        resourceId="agent-1"
        resourceName="Helper"
      />,
    )
    const dialog = await screen.findByRole('dialog', { name: 'Helper 공유' })
    for (const token of DIALOG_HEIGHT.fixed.split(' ')) expect(dialog).toHaveClass(token)
    const actions = await within(dialog).findByTestId('share-form-actions')
    expect(actions).toHaveClass('sticky', 'bottom-0')
    expect(within(actions).getByRole('button', { name: '공유' })).toBeInTheDocument()
    // The body, not the dialog, scrolls: the header stays put.
    expect(actions.closest('.moldy-dialog-body')).toHaveClass('overflow-y-auto')
  })
})
