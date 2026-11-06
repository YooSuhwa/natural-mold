import { render, screen, waitFor } from '../test-utils'
import { SystemStatusContent } from '@/app/settings/system-status/_components/system-status-content'
import type { SystemStatusOut } from '@/lib/types/system-status'

const mockUseSession = vi.fn()
const getStatus = vi.fn()
const replace = vi.fn()

vi.mock('@/lib/auth/session', () => ({
  useSession: () => mockUseSession(),
}))

vi.mock('@/lib/api/system-status', () => ({
  systemStatusApi: { get: () => getStatus() },
}))

vi.mock('next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('next/navigation')>()),
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
}))

const STATUS: SystemStatusOut = {
  profile: 'kubernetes',
  verdict: 'error',
  components: [
    {
      key: 'database',
      requirement: 'required',
      found: 'ok',
      verdict: 'ok',
      detail: 'PostgreSQL 16.15',
    },
    {
      key: 'object_store',
      requirement: 'required',
      found: 'error',
      verdict: 'error',
      detail: 'this profile needs S3-compatible storage (STORAGE_BACKEND=s3)',
    },
    {
      key: 'pii_detector',
      requirement: 'optional',
      found: 'off',
      verdict: 'ok',
      detail: 'no model service; the rules pack runs',
    },
  ],
  workers: [
    {
      worker_id: 'host:123:abc',
      hostname: 'host',
      job_types: ['system.probe'],
      started_at: '2026-11-06T00:00:00Z',
      heartbeat_at: '2026-11-06T00:00:10Z',
      live: true,
    },
  ],
  queue: { queued: 3, running: 1, overdue: 0, failed_last_day: 2 },
  checked_at: '2026-11-06T00:00:12Z',
}

beforeEach(() => {
  vi.clearAllMocks()
})

it('shows each component graded against the profile, and the job queue', async () => {
  mockUseSession.mockReturnValue({ data: { is_super_user: true }, isPending: false })
  getStatus.mockResolvedValue(STATUS)

  render(<SystemStatusContent />)

  expect(await screen.findByText('필수 구성 요소에 문제가 있습니다.')).toBeInTheDocument()
  expect(screen.getByText('Kubernetes 격리')).toBeInTheDocument()
  const storage = document.querySelector('[data-component="object_store"]')
  expect(storage).toHaveTextContent('파일 저장소')
  expect(storage).toHaveTextContent('STORAGE_BACKEND=s3')
  expect(storage?.querySelector('[data-verdict]')).toHaveAttribute('data-verdict', 'error')
  // Optional and off is fine.
  const detector = document.querySelector('[data-component="pii_detector"]')
  expect(detector).toHaveTextContent('꺼짐')
  expect(detector?.querySelector('[data-verdict]')).toHaveAttribute('data-verdict', 'ok')
  expect(document.querySelector('[data-queue="queued"]')).toHaveTextContent('3')
  expect(document.querySelector('[data-queue="failed_last_day"]')).toHaveTextContent('2')
  expect(screen.getByText('host:123:abc')).toBeInTheDocument()
})

it('sends a non-operator home without asking the backend', async () => {
  mockUseSession.mockReturnValue({ data: { is_super_user: false }, isPending: false })

  render(<SystemStatusContent />)

  await waitFor(() => expect(replace).toHaveBeenCalledWith('/'))
  expect(getStatus).not.toHaveBeenCalled()
})

it('says so when the status cannot be loaded', async () => {
  mockUseSession.mockReturnValue({ data: { is_super_user: true }, isPending: false })
  getStatus.mockRejectedValue(new Error('boom'))

  render(<SystemStatusContent />)

  expect(await screen.findByText('시스템 상태를 불러오지 못했습니다.')).toBeInTheDocument()
})
