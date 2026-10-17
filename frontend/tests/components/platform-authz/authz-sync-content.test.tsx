import { AuthzSyncContent } from '@/features/platform-authz/components/authz-sync-content'
import { ApiError } from '@/lib/api/errors'
import { ACTIVE_POLL_MS, IDLE_POLL_MS, statusPollInterval } from '@/lib/hooks/use-admin-authz'
import type { AuthzOpsStatus, ReconcileRunView } from '@/lib/types/admin-authz'
import { render, screen, userEvent, waitFor, within } from '../../test-utils'

const api = vi.hoisted(() => ({
  status: vi.fn(),
  failures: vi.fn(),
  retry: vi.fn(),
  reconcile: vi.fn(),
  run: vi.fn(),
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

vi.mock('@/lib/api/admin-authz', () => ({ adminAuthzApi: api }))
vi.mock('sonner', () => ({ toast }))

const run: ReconcileRunView = {
  id: 'run-1',
  status: 'succeeded',
  trigger: 'scheduled',
  mode: 'repair',
  partitions_done: 3,
  partitions_total: 3,
  report: {
    expected: 10,
    stored: 10,
    missing: 1,
    extra: 1,
    changed: 0,
    held: 0,
    queued: 2,
    failed: 0,
    diff: 2,
    repaired: 2,
    deferred: 0,
  },
  error: null,
  created_at: '2026-10-17T03:00:00Z',
  started_at: '2026-10-17T03:00:00Z',
  finished_at: '2026-10-17T03:01:00Z',
}

function status(overrides: Partial<AuthzOpsStatus> = {}): AuthzOpsStatus {
  return {
    mode: 'shadow',
    sync_enabled: true,
    openfga: {
      status: 'ok',
      latency_ms: 12,
      checked_at: '2026-10-17T09:00:00Z',
      last_success_at: '2026-10-17T09:00:00Z',
    },
    outbox: { pending: 7, retrying: 3, failed: 1, lag_seconds: 4.2 },
    revocations: { pending: 2, stuck: 1, oldest_seconds: 700 },
    reconcile: { active: null, last: run },
    shadow: [{ type: 'agent', legacy_only: 3, fga_only: 1 }],
    delegations: { broken_links: 2, broken_last_24h: 1 },
    retention: {
      enabled: true,
      schedule: '20 3 * * *',
      last_run_at: '2026-10-17T03:20:00Z',
      purges_last_24h: 4,
    },
    jobs: [
      { id: 'authz_outbox_drain', scheduled: true, schedule: 'interval[0:00:02]' },
      { id: 'retention_sweep', scheduled: false, schedule: null },
    ],
    ...overrides,
  }
}

const failure = {
  id: 'ob-1',
  tenant_id: '8f2c0000-0000-0000-0000-000000000000',
  resource_type: 'agent',
  resource_id: 'aaaa1111-0000-0000-0000-000000000000',
  op: 'write',
  attempts: 12,
  error_type: 'TimeoutError',
  created_at: '2026-10-17T08:00:00Z',
  next_attempt_at: '2026-10-17T09:05:00Z',
}

describe('AuthzSyncContent (S27)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.failures.mockResolvedValue({ items: [failure] })
  })

  it('shows backlog, last reconcile, failures with ids only, shadow and jobs', async () => {
    api.status.mockResolvedValue(status())
    render(<AuthzSyncContent />)

    expect(await screen.findByRole('heading', { name: '권한 동기화 상태' })).toBeInTheDocument()
    expect(await screen.findByText('7')).toBeInTheDocument()
    expect(screen.getByText('가장 오래된 항목 4초')).toBeInTheDocument()
    expect(screen.getByText('불일치 2건 · 자동 복구 2건 · 남은 항목 0건')).toBeInTheDocument()
    expect(screen.getByText('10분 넘게 남은 항목 1건')).toBeInTheDocument()
    const row = (await screen.findByText('agent aaaa1111')).closest('tr')
    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).getByText('8f2c0000')).toBeInTheDocument()
    expect(within(row as HTMLElement).getByText('OpenFGA 시간 초과')).toBeInTheDocument()
    expect(screen.getByText('legacy만 허용')).toBeInTheDocument()
    expect(screen.getByText('아웃박스 처리')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the connection banner with the last success time when OpenFGA is down', async () => {
    api.status.mockResolvedValue(
      status({
        openfga: {
          status: 'down',
          latency_ms: null,
          checked_at: '2026-10-17T09:00:00Z',
          last_success_at: '2026-10-17T08:00:00Z',
        },
      }),
    )
    render(<AuthzSyncContent />)
    const banner = await screen.findByRole('alert')
    expect(within(banner).getByText('OpenFGA에 연결할 수 없어요')).toBeInTheDocument()
    expect(within(banner).getByText(/마지막 성공/)).toBeInTheDocument()
  })

  it('confirms before reconciling and maps the running conflict', async () => {
    api.status.mockResolvedValue(status())
    api.reconcile.mockRejectedValueOnce(new ApiError(409, 'authz_reconcile_running', 'x'))
    api.reconcile.mockResolvedValueOnce({ job_id: 'run-2' })
    const user = userEvent.setup()
    render(<AuthzSyncContent />)

    await user.click(await screen.findByRole('button', { name: '대조 실행' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('권한 대조를 실행할까요?')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(api.reconcile).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '대조 실행' })).toHaveFocus()

    await user.click(screen.getByRole('button', { name: '대조 실행' }))
    await user.click(await screen.findByRole('button', { name: '실행' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('이미 대조가 실행 중이에요'))

    await user.click(screen.getByRole('button', { name: '대조 실행' }))
    await user.click(await screen.findByRole('button', { name: '실행' }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('권한 대조를 시작했어요'))
  })

  it('disables the run while one is active and shows company progress', async () => {
    const active: ReconcileRunView = {
      ...run,
      id: 'run-3',
      status: 'running',
      trigger: 'manual',
      partitions_done: 2,
      partitions_total: 5,
      report: null,
      finished_at: null,
    }
    api.status.mockResolvedValue(status({ reconcile: { active, last: run } }))
    render(<AuthzSyncContent />)
    expect(await screen.findByRole('button', { name: '대조 진행 중' })).toBeDisabled()
    expect(screen.getByText('회사 2/5 확인 중')).toBeInTheDocument()
    expect(statusPollInterval(status({ reconcile: { active, last: null } }))).toBe(ACTIVE_POLL_MS)
    expect(statusPollInterval(status())).toBe(IDLE_POLL_MS)
  })

  it('keeps actions disabled when sync is off and retries a failure when on', async () => {
    api.status.mockResolvedValueOnce(
      status({
        mode: 'legacy',
        sync_enabled: false,
        openfga: { status: 'disabled', latency_ms: null, checked_at: null, last_success_at: null },
      }),
    )
    const { unmount } = render(<AuthzSyncContent />)
    expect(await screen.findByRole('button', { name: '대조 실행' })).toBeDisabled()
    expect(await screen.findByRole('button', { name: 'agent aaaa1111 다시 시도' })).toBeDisabled()
    expect(screen.getByText(/legacy 모드라 OpenFGA를 사용하지 않아요/)).toBeInTheDocument()
    unmount()

    api.status.mockResolvedValue(status())
    api.retry.mockResolvedValue(failure)
    const user = userEvent.setup()
    render(<AuthzSyncContent />)
    const retry = await screen.findByRole('button', { name: 'agent aaaa1111 다시 시도' })
    await waitFor(() => expect(retry).toBeEnabled())
    await user.click(retry)
    await waitFor(() => expect(api.retry).toHaveBeenCalledWith('ob-1'))
    expect(toast.success).toHaveBeenCalledWith('다시 시도하도록 예약했어요')
  })
})
