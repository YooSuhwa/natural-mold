import { TenantListContent } from '@/features/platform-tenants/components/tenant-list-content'
import { platformTenantsApi } from '@/lib/api/platform-tenants'
import { ApiError } from '@/lib/api/errors'
import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { tenantFixture, usageFixture } from './fixtures'

vi.mock('@/lib/api/platform-tenants', async () => {
  const { platformApiMock } = await import('./fixtures')
  return { platformTenantsApi: platformApiMock(), tenantVersionHeader: vi.fn() }
})
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('sonner', () => ({ toast }))

const api = vi.mocked(platformTenantsApi)
const gana = tenantFixture()
const trial = tenantFixture({
  id: '33333333-3333-4333-8333-333333333333',
  name: '체험 고객',
  slug: 'trial-07',
  status: 'suspended',
  limits: {
    member_limit: null,
    monthly_spend_limit: null,
    concurrent_run_limit: null,
    allow_system_models: false,
  },
  organization_count: 1,
  active_member_count: 3,
})

function page(items = [gana, trial]) {
  return { items, next_cursor: null, has_more: false }
}

async function openMenu(name: string) {
  await userEvent.click(await screen.findByRole('button', { name: `${name} 메뉴` }))
}

describe('TenantListContent (S03)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.usage.mockResolvedValue(usageFixture)
  })

  it('shows a loading skeleton, then metadata rows without company content', async () => {
    api.list.mockResolvedValue(page())
    render(<TenantListContent />)
    expect(screen.getByTestId('tenant-list-loading')).toBeInTheDocument()
    const row = (await screen.findByRole('link', { name: '가나전자' })).closest('tr') as HTMLElement
    expect(within(row).getByText('gana')).toBeInTheDocument()
    expect(within(row).getByText('412 / 500')).toBeInTheDocument()
    expect(within(row).getByText('$1,000.00')).toBeInTheDocument()
    expect(within(row).getByText('이용 중')).toBeInTheDocument()
    const other = screen.getByRole('link', { name: '체험 고객' }).closest('tr') as HTMLElement
    expect(within(other).getAllByText('제한 없음')).toHaveLength(1)
    expect(within(other).getByText('정지')).toBeInTheDocument()
  })

  it('filters by status and searches by name or slug', async () => {
    api.list.mockResolvedValue(page())
    render(<TenantListContent />)
    await screen.findByRole('link', { name: '가나전자' })
    await userEvent.click(screen.getByRole('button', { name: '삭제 예정' }))
    await waitFor(() =>
      expect(api.list).toHaveBeenLastCalledWith({
        status: 'deletion_scheduled',
        search: '',
        cursor: null,
      }),
    )
    expect(screen.getByRole('button', { name: '삭제 예정' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await userEvent.type(screen.getByRole('textbox', { name: '회사 검색' }), 'ga')
    await waitFor(() =>
      expect(api.list).toHaveBeenLastCalledWith({
        status: 'deletion_scheduled',
        search: 'ga',
        cursor: null,
      }),
    )
    // Debounced (250ms): no request per keystroke.
    expect(api.list).not.toHaveBeenCalledWith(expect.objectContaining({ search: 'g' }))
  })

  it('shows the shared error state with retry', async () => {
    api.list.mockRejectedValueOnce(new ApiError(503, 'authz_unavailable', 'down'))
    render(<TenantListContent />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveAttribute('data-error-kind', 'authz_unavailable')
    api.list.mockResolvedValue(page())
    await userEvent.click(within(alert).getByRole('button', { name: '다시 시도' }))
    expect(await screen.findByRole('link', { name: '가나전자' })).toBeInTheDocument()
  })

  it('shows the empty state', async () => {
    api.list.mockResolvedValue(page([]))
    render(<TenantListContent />)
    expect(await screen.findByText('조건에 맞는 회사가 없어요.')).toBeInTheDocument()
  })

  it('suspends only after the slug is typed, showing members and active runs', async () => {
    api.list.mockResolvedValue(page())
    api.suspend.mockResolvedValue({ ...gana, status: 'suspended' })
    render(<TenantListContent />)
    await openMenu('가나전자')
    await userEvent.click(await screen.findByRole('menuitem', { name: '정지' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText(/구성원 412명이 영향을 받아요/)).toBeInTheDocument()
    expect(await within(dialog).findByText(/진행 중인 실행 3건/)).toBeInTheDocument()
    const confirm = within(dialog).getByRole('button', { name: '정지' })
    expect(confirm).toBeDisabled()
    const input = within(dialog).getByLabelText('확인을 위해 회사 슬러그 gana를 입력하세요')
    await userEvent.type(input, 'gan')
    expect(confirm).toBeDisabled()
    await userEvent.type(input, 'a')
    expect(confirm).toBeEnabled()
    await userEvent.click(confirm)
    await waitFor(() => expect(api.suspend).toHaveBeenCalledWith(gana.id, gana.updated_at))
    expect(toast.success).toHaveBeenCalledWith('가나전자 이용을 정지했어요.')
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  })

  it('schedules a deletion with the slug and keeps the dialog open on failure', async () => {
    api.list.mockResolvedValue(page())
    api.scheduleDeletion.mockRejectedValueOnce(
      new ApiError(409, 'tenant_deletion_slug_mismatch', 'mismatch'),
    )
    render(<TenantListContent />)
    await openMenu('가나전자')
    await userEvent.click(await screen.findByRole('menuitem', { name: '삭제 예약' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(
      within(dialog).getByText(/30일 유예 후 회사의 모든 데이터가 영구 삭제돼요/),
    ).toBeInTheDocument()
    await userEvent.type(within(dialog).getByRole('textbox'), 'gana')
    await userEvent.click(within(dialog).getByRole('button', { name: '삭제 예약' }))
    await waitFor(() =>
      expect(api.scheduleDeletion).toHaveBeenCalledWith(gana.id, 'gana', gana.updated_at),
    )
    expect(
      await within(dialog).findByText('입력한 슬러그가 회사 슬러그와 달라요.'),
    ).toBeInTheDocument()
    expect(toast.error).toHaveBeenCalled()
    await userEvent.click(within(dialog).getByRole('button', { name: '취소' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  })

  it('resumes a suspended company and starts an export from the row menu', async () => {
    api.list.mockResolvedValue(page())
    api.resume.mockResolvedValue({ ...trial, status: 'active' })
    api.startExport.mockResolvedValue({ job_id: 'job-1' })
    render(<TenantListContent />)
    await openMenu('체험 고객')
    expect(screen.queryByRole('menuitem', { name: '정지' })).not.toBeInTheDocument()
    await userEvent.click(await screen.findByRole('menuitem', { name: '재개' }))
    await waitFor(() => expect(api.resume).toHaveBeenCalledWith(trial.id, trial.updated_at))
    await openMenu('체험 고객')
    await userEvent.click(await screen.findByRole('menuitem', { name: '데이터 내보내기' }))
    await waitFor(() => expect(api.startExport).toHaveBeenCalledWith(trial.id))
    expect(toast.success).toHaveBeenCalledWith('체험 고객 데이터 내보내기를 시작했어요.')
  })
})
