import { TenantDetailContent } from '@/features/platform-tenants/components/tenant-detail-content'
import { platformTenantsApi } from '@/lib/api/platform-tenants'
import { ApiError } from '@/lib/api/errors'
import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { exportFixture, tenantFixture, usageFixture } from './fixtures'

vi.mock('@/lib/api/platform-tenants', async () => {
  const { platformApiMock } = await import('./fixtures')
  return { platformTenantsApi: platformApiMock(), tenantVersionHeader: vi.fn() }
})
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('sonner', () => ({ toast }))
const download = vi.hoisted(() => vi.fn())
vi.mock('@/lib/hooks/use-api-object-url', () => ({ downloadApiFile: download }))

const api = vi.mocked(platformTenantsApi)
const tenant = tenantFixture()
const DAY = 24 * 60 * 60 * 1000

function section(name: string): HTMLElement {
  return screen.getByRole('heading', { name }).closest('section') as HTMLElement
}

describe('TenantDetailContent (S04)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.get.mockResolvedValue(tenant)
    api.usage.mockResolvedValue(usageFixture)
    api.deletion.mockResolvedValue({ tenant, deletion_due_at: null })
    api.exports.mockResolvedValue({ items: [exportFixture()] })
  })

  it('shows loading, then the overview with this month usage and limits', async () => {
    render(<TenantDetailContent tenantId={tenant.id} />)
    expect(screen.getByTestId('tenant-detail-loading')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '가나전자' })).toBeInTheDocument()
    const overview = section('개요')
    expect(within(overview).getByText('412 / 500')).toBeInTheDocument()
    expect(await within(overview).findByText('$812.50 / $1,000.00')).toBeInTheDocument()
    expect(within(overview).getByText('3 / 50')).toBeInTheDocument()
    expect(within(overview).getByText('5')).toBeInTheDocument()
    expect(screen.queryByText(/에이전트|대화 목록/)).not.toBeInTheDocument()
  })

  it('shows the shared error state with a way back', async () => {
    api.get.mockRejectedValue(new ApiError(404, 'not_found', 'missing'))
    render(<TenantDetailContent tenantId={tenant.id} />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveAttribute('data-error-kind', 'not_found')
    expect(within(alert).getByRole('link', { name: '회사 목록' })).toHaveAttribute(
      'href',
      '/settings/admin/tenants',
    )
  })

  it('saves limits with the company version; empty means no limit', async () => {
    api.update.mockResolvedValue(tenant)
    render(<TenantDetailContent tenantId={tenant.id} />)
    const limits = await screen.findByRole('heading', { name: '한도' })
    const form = limits.closest('section') as HTMLElement
    const members = within(form).getByLabelText('구성원 한도')
    await userEvent.clear(members)
    await userEvent.type(within(form).getByLabelText('동시 실행 한도'), '.5')
    expect(within(form).getByRole('button', { name: '저장' })).toBeDisabled()
    await userEvent.clear(within(form).getByLabelText('동시 실행 한도'))
    await userEvent.type(within(form).getByLabelText('동시 실행 한도'), '10')
    await userEvent.click(within(form).getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(api.update).toHaveBeenCalledWith(
        tenant.id,
        {
          name: '가나전자',
          member_limit: null,
          monthly_spend_limit: 1000,
          concurrent_run_limit: 10,
          allow_system_models: true,
        },
        tenant.updated_at,
      ),
    )
    expect(toast.success).toHaveBeenCalledWith('한도를 저장했어요.')
  })

  it('lists exports, downloads a finished one and blocks a second live export', async () => {
    api.exports.mockResolvedValue({
      items: [
        exportFixture({
          id: 'live',
          status: 'running',
          progress: 40,
          download_available: false,
          size_bytes: null,
          expires_at: null,
        }),
        exportFixture(),
      ],
    })
    render(<TenantDetailContent tenantId={tenant.id} />)
    const data = (await screen.findByRole('heading', { name: '데이터' })).closest(
      'section',
    ) as HTMLElement
    expect(await within(data).findByText('진행 중 40%')).toBeInTheDocument()
    expect(within(data).getByText('완료')).toBeInTheDocument()
    expect(within(data).getByRole('button', { name: '내보내기 시작' })).toBeDisabled()
    download.mockResolvedValue(undefined)
    await userEvent.click(within(data).getByRole('button', { name: '다운로드' }))
    expect(download).toHaveBeenCalledWith(
      `/x/${tenant.id}/22222222-2222-4222-8222-222222222222`,
      `tenant-${tenant.id}-export-22222222-2222-4222-8222-222222222222.zip`,
    )
  })

  it('shows the D-n badge for a scheduled deletion and cancels it', async () => {
    const scheduled = tenantFixture({ status: 'deletion_scheduled' })
    const due = new Date(Date.now() + 12 * DAY - 60_000).toISOString()
    api.get.mockResolvedValue(scheduled)
    api.deletion.mockResolvedValue({ tenant: scheduled, deletion_due_at: due })
    api.cancelDeletion.mockResolvedValue({
      tenant: { ...scheduled, status: 'suspended' },
      deletion_due_at: null,
    })
    render(<TenantDetailContent tenantId={tenant.id} />)
    expect(await screen.findByText('삭제 예정(D-12)')).toBeInTheDocument()
    const data = section('데이터')
    expect(within(data).getByText(/영구 삭제돼요\(D-12\)/)).toBeInTheDocument()
    await userEvent.click(within(data).getByRole('button', { name: '예약 취소' }))
    await waitFor(() =>
      expect(api.cancelDeletion).toHaveBeenCalledWith(scheduled.id, scheduled.updated_at),
    )
    expect(toast.success).toHaveBeenCalledWith(
      '가나전자 삭제 예약을 취소했어요. 회사는 정지 상태로 남아요.',
    )
  })

  it('opens the slug confirmation for deletion from the data section', async () => {
    render(<TenantDetailContent tenantId={tenant.id} />)
    const data = (await screen.findByRole('heading', { name: '데이터' })).closest(
      'section',
    ) as HTMLElement
    await userEvent.click(within(data).getByRole('button', { name: '삭제 예약' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByRole('button', { name: '삭제 예약' })).toBeDisabled()
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(api.scheduleDeletion).not.toHaveBeenCalled()
  })
})
