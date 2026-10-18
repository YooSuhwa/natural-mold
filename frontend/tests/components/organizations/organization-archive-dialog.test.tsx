import { render, screen, userEvent, waitFor } from '../../test-utils'
import { toast } from 'sonner'
import { OrganizationArchiveAction } from '@/features/organizations/components/organization-archive-dialog'
import type { TenantOrganizationSummary } from '@/lib/types/organization'
import { ApiError } from '@/lib/api/errors'
import { DIALOG_HEIGHT } from '@/lib/design-tokens'
import { trapConsole } from '../sharing/sharing-mocks'
import { orgApiMock } from './org-admin-mocks'

vi.mock('@/lib/api/organizations', async () => (await import('./org-admin-mocks')).orgApiMock)
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { tenantOrganizationsApi } = orgApiMock

function org(overrides: Partial<TenantOrganizationSummary> = {}): TenantOrganizationSummary {
  return {
    id: 'org-2',
    name: 'Sales',
    slug: 'sales',
    status: 'active',
    is_default: false,
    default_member_role: 'member',
    member_count: 12,
    administrator_user_id: null,
    updated_at: '2026-10-16T00:00:00Z',
    ...overrides,
  }
}

describe('OrganizationArchiveAction', () => {
  let consoleTrap: ReturnType<typeof trapConsole>

  beforeEach(() => {
    vi.clearAllMocks()
    consoleTrap = trapConsole()
  })

  afterEach(() => {
    consoleTrap.assertClean()
    consoleTrap.restore()
  })

  it('offers nothing for a default organization (policy A)', () => {
    render(
      <OrganizationArchiveAction tenantId="tenant-1" organization={org({ is_default: true })} />,
    )
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('offers nothing for an archived or suspended organization', () => {
    render(
      <OrganizationArchiveAction tenantId="tenant-1" organization={org({ status: 'archived' })} />,
    )
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('archives after the exact organization name is typed', async () => {
    tenantOrganizationsApi.archive.mockResolvedValue(org({ status: 'archived' }))
    render(<OrganizationArchiveAction tenantId="tenant-1" organization={org()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Sales 보관' }))
    const dialog = await screen.findByRole('dialog', { name: 'Sales 조직을 보관할까요?' })
    expect(dialog).toHaveTextContent('구성원 12명이 조직에 접근할 수 없게 돼요')
    expect(dialog).toHaveTextContent('진행 중인 실행은 중지돼요')
    // Short confirm: content-sized, no large empty body (capture 06).
    for (const token of DIALOG_HEIGHT.compact.split(' ')) expect(dialog).toHaveClass(token)
    expect(dialog).not.toHaveClass('h-[480px]')
    const submit = screen.getByRole('button', { name: '보관' })
    expect(submit).toBeDisabled()
    await userEvent.type(screen.getByRole('textbox'), 'Sales')
    await userEvent.click(submit)
    await waitFor(() =>
      expect(tenantOrganizationsApi.archive).toHaveBeenCalledWith(
        'tenant-1',
        'org-2',
        { confirm_name: 'Sales' },
        '2026-10-16T00:00:00Z',
      ),
    )
    expect(toast.success).toHaveBeenCalledWith('Sales 조직을 보관했어요.')
  })

  it('shows the localized reason when the archive is refused', async () => {
    tenantOrganizationsApi.archive.mockRejectedValue(new ApiError(409, 'last_organization', 'x'))
    render(<OrganizationArchiveAction tenantId="tenant-1" organization={org()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Sales 보관' }))
    await userEvent.type(await screen.findByRole('textbox'), 'Sales')
    await userEvent.click(screen.getByRole('button', { name: '보관' }))
    expect(await screen.findByText('회사의 마지막 조직은 보관할 수 없습니다.')).toBeInTheDocument()
  })
})
