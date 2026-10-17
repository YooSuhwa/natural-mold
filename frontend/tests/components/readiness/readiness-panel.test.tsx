import { toast } from 'sonner'

import { ReadinessDialog } from '@/components/readiness/readiness-dialog'
import type { AgentReadiness } from '@/lib/types/readiness'
import { render, screen, userEvent, waitFor } from '../../test-utils'

const readiness = vi.hoisted(() => ({ get: vi.fn(), notifyOwner: vi.fn(), summary: vi.fn() }))
const bindings = vi.hoisted(() => ({ bind: vi.fn(), list: vi.fn(), unbind: vi.fn() }))
const credentials = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('@/lib/api/readiness', () => ({ readinessApi: readiness }))
vi.mock('@/lib/api/credential-bindings', () => ({ credentialBindingsApi: bindings }))
vi.mock('@/lib/api/credentials', () => ({ credentialsApi: credentials, systemCredentialsApi: {} }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const body: AgentReadiness = {
  overall: 'needs_connection',
  identity_mode: 'per_user',
  items: [
    {
      kind: 'mcp_server',
      id: 'srv-1',
      name: 'Slack',
      required: true,
      result: 'connect_by_me',
      credential: { mode: 'per_user', status: 'missing', definition_key: 'http_bearer' },
      action: { type: 'connect' },
    },
    {
      kind: 'tool',
      name: 'ERP 잔여 연차 조회',
      required: false,
      result: 'owner_must_connect',
      action: { type: 'notify_owner' },
    },
    {
      kind: 'tool',
      count: 1,
      required: false,
      result: 'no_permission',
      action: { type: 'request_access', via: 'agent_manager' },
    },
  ],
}

function credential(id: string, name: string, definition_key: string) {
  return { id, name, definition_key, status: 'active', is_system: false, user_id: 'me' }
}

describe('ReadinessDialog (S21) and the per-user connection dialog (S23)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    readiness.get.mockResolvedValue(body)
    credentials.list.mockResolvedValue([
      credential('c-1', '내 Slack', 'http_bearer'),
      credential('c-2', '다른 키', 'naver_search'),
    ])
  })

  it('lists every item with its state and keeps start off until required items connect', async () => {
    render(
      <ReadinessDialog open onOpenChange={vi.fn()} agentId="agent-1" agentName="인사 도우미" />,
    )
    expect(await screen.findByText('Slack')).toBeInTheDocument()
    expect(screen.getByText('인사 도우미 시작 전 점검')).toBeInTheDocument()
    expect(screen.getByText('필수 항목 1개를 연결하면 바로 쓸 수 있어요')).toBeInTheDocument()
    expect(screen.getByText('도구 1개')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '확인', exact: true })).toBeDisabled()
  })

  it('connects my credential of the required type to the per-user resource', async () => {
    bindings.bind.mockResolvedValue({ id: 'b-1' })
    const onConnected = vi.fn()
    render(
      <ReadinessDialog open onOpenChange={vi.fn()} agentId="agent-1" onConnected={onConnected} />,
    )
    await userEvent.click(await screen.findByRole('button', { name: '연결하기' }))
    expect(await screen.findByText('내 Slack')).toBeInTheDocument()
    expect(screen.queryByText('다른 키')).not.toBeInTheDocument() // other type filtered out
    await userEvent.click(screen.getByRole('button', { name: '내 Slack 연결' }))
    await waitFor(() => expect(bindings.bind).toHaveBeenCalledWith('mcp_server', 'srv-1', 'c-1'))
    expect(toast.success).toHaveBeenCalledWith('연결했어요')
    expect(onConnected).toHaveBeenCalled()
    await waitFor(() => expect(readiness.get).toHaveBeenCalledTimes(2)) // re-checked
  })

  it('notifies the owner once and requests access for counted items via the agent', async () => {
    readiness.notifyOwner.mockResolvedValue({ notified: 1 })
    render(<ReadinessDialog open onOpenChange={vi.fn()} agentId="agent-1" />)
    await userEvent.click(await screen.findByRole('button', { name: '소유자에게 알림' }))
    await waitFor(() => expect(readiness.notifyOwner).toHaveBeenCalledWith('agent-1'))
    expect(await screen.findByRole('button', { name: '알렸어요' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: '권한 요청' }))
    expect(await screen.findByRole('button', { name: '요청 보내기' })).toBeInTheDocument()
  })

  it('keeps the notify button when no notice could be written', async () => {
    readiness.notifyOwner.mockResolvedValue({ notified: 0 })
    render(<ReadinessDialog open onOpenChange={vi.fn()} agentId="agent-1" />)
    await userEvent.click(await screen.findByRole('button', { name: '소유자에게 알림' }))
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        '소유자에게 알리지 못했어요. 소유자 계정을 확인할 수 없어요.',
      ),
    )
    expect(screen.getByRole('button', { name: '소유자에게 알림' })).toBeEnabled()
  })

  it('offers a retry when the check cannot be loaded', async () => {
    readiness.get.mockRejectedValue(new Error('down'))
    render(<ReadinessDialog open onOpenChange={vi.fn()} agentId="agent-1" />)
    expect(await screen.findByText('점검 결과를 불러오지 못했어요')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeInTheDocument()
  })
})
