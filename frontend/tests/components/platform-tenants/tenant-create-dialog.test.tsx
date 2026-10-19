import { TenantCreateDialog } from '@/features/platform-tenants/components/tenant-create-dialog'
import { platformTenantsApi } from '@/lib/api/platform-tenants'
import { ApiError } from '@/lib/api/errors'
import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { tenantFixture } from './fixtures'

vi.mock('@/lib/api/platform-tenants', async () => {
  const { platformApiMock } = await import('./fixtures')
  return { platformTenantsApi: platformApiMock(), tenantVersionHeader: vi.fn() }
})
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('sonner', () => ({ toast }))

const api = vi.mocked(platformTenantsApi)

async function fill() {
  await userEvent.type(screen.getByLabelText(/회사 이름/), '가나전자')
  await userEvent.type(screen.getByLabelText(/^슬러그/), 'gana')
  await userEvent.type(screen.getByLabelText(/첫 회사 관리자 이메일/), 'it-admin@example.com')
  await userEvent.type(screen.getByLabelText('구성원 한도'), '500')
}

describe('TenantCreateDialog (S04 회사 만들기)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('validates the slug and email before enabling submit', async () => {
    render(<TenantCreateDialog onClose={vi.fn()} />)
    const submit = screen.getByRole('button', { name: '만들고 초대 보내기' })
    expect(submit).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/^슬러그/), '-x')
    expect(screen.getByText(/하이픈으로 시작하거나 끝날 수 없어요/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(/첫 회사 관리자 이메일/), 'nope')
    expect(screen.getByText('이메일 형식이 올바르지 않아요.')).toBeInTheDocument()
    expect(submit).toBeDisabled()
  })

  it('creates the company and offers the one-time invitation code without showing it', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    api.create.mockResolvedValue({
      tenant: tenantFixture(),
      organization: { id: 'org-1', name: '기본 조직' },
      invitation: {
        id: 'inv-1',
        email: 'it-admin@example.com',
        role: 'admin',
        expires_at: '2026-10-25T00:00:00Z',
        created_at: '2026-10-18T00:00:00Z',
        status: 'pending',
        token: 'tv1.secret-token',
      },
    })
    render(<TenantCreateDialog onClose={vi.fn()} />)
    await fill()
    await userEvent.click(screen.getByRole('button', { name: '만들고 초대 보내기' }))
    await waitFor(() =>
      expect(api.create).toHaveBeenCalledWith({
        name: '가나전자',
        slug: 'gana',
        first_admin_email: 'it-admin@example.com',
        first_organization_name: '기본 조직',
        member_limit: 500,
        monthly_spend_limit: null,
        concurrent_run_limit: null,
        allow_system_models: false,
      }),
    )
    const dialog = screen.getByRole('dialog')
    expect(await within(dialog).findByText(/it-admin@example.com 앞으로/)).toBeInTheDocument()
    expect(within(dialog).queryByText(/secret-token/)).not.toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: '초대 코드 복사' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('tv1.secret-token'))
  })

  it('shows a duplicate slug inline', async () => {
    api.create.mockRejectedValue(new ApiError(409, 'duplicate_tenant_slug', 'taken'))
    render(<TenantCreateDialog onClose={vi.fn()} />)
    await fill()
    await userEvent.click(screen.getByRole('button', { name: '만들고 초대 보내기' }))
    expect(
      await screen.findByText('이미 쓰고 있는 슬러그예요. 다른 슬러그를 입력하세요.'),
    ).toBeInTheDocument()
  })
})
