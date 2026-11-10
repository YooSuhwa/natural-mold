import { toast } from 'sonner'

import { ApprovalsPageClient } from '@/app/approvals/_components/approvals-page-client'
import { ApiError } from '@/lib/api/client'
import type { ApprovalView, EmergencyStopView } from '@/lib/types/approval'
import { render, screen, userEvent, waitFor, within } from '../test-utils'

const approvals = vi.hoisted(() => ({
  list: vi.fn(),
  pendingCount: vi.fn(),
  decide: vi.fn(),
  runs: vi.fn(),
}))
const stops = vi.hoisted(() => ({ list: vi.fn(), stop: vi.fn(), release: vi.fn() }))

vi.mock('@/lib/api/approvals', () => ({ approvalsApi: approvals, emergencyStopsApi: stops }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function request(overrides: Partial<ApprovalView> = {}): ApprovalView {
  return {
    id: 'apr-1',
    status: 'pending',
    source: 'trigger',
    agent_id: 'agent-1',
    agent_name: '메일 비서',
    conversation_id: 'conv-1',
    trigger_id: 'trig-1',
    actions: [
      {
        name: 'send_email',
        args: { to: 'a@example.com' },
        description: null,
        allowed_decisions: ['approve', 'edit', 'reject'],
      },
      {
        name: 'execute_in_skill',
        args: {},
        description: null,
        allowed_decisions: ['approve', 'reject'],
      },
    ],
    attempt: 1,
    policy_version: 1,
    channel: null,
    reason: null,
    created_at: '2026-11-07T00:00:00Z',
    expires_at: '2099-11-08T00:00:00Z',
    decided_at: null,
    ...overrides,
  }
}

function stop(): EmergencyStopView {
  return {
    id: 'stop-1',
    scope_type: 'agent',
    scope_id: 'agent-1',
    scope_name: '메일 비서',
    status: 'active',
    reason: '오발송 의심',
    stopped_by: 'u1',
    stopped_at: '2026-11-07T00:00:00Z',
    released_by: null,
    released_at: null,
    release_reason: null,
    state: {
      block_received: true,
      new_calls_blocked: true,
      runs_cancel_requested: 2,
      runs_cancel_confirmed: 1,
      schedules_paused: 1,
      deployments_disabled: 0,
      approvals_canceled: 3,
      external_side_effects: 'unknown',
    },
  }
}

describe('ApprovalsPageClient', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    approvals.list.mockResolvedValue({ items: [request()] })
    approvals.runs.mockResolvedValue({ items: [] })
    stops.list.mockResolvedValue({ items: [stop()] })
  })

  it('shows what to decide and sends one decision per action, edits included', async () => {
    approvals.decide.mockResolvedValue(request({ status: 'edited' }))
    render(<ApprovalsPageClient />)

    expect(await screen.findByText('메일 비서')).toBeInTheDocument()
    expect(screen.getByText('예약')).toBeInTheDocument()
    const mail = screen.getByRole('group', { name: 'send_email 결정' })
    await userEvent.click(within(mail).getByRole('button', { name: '수정' }))
    const editor = screen.getByLabelText('send_email에 보낼 인자(JSON)')
    await userEvent.clear(editor)
    await userEvent.type(editor, '{{"to":"b@example.com"}')
    const skill = screen.getByRole('group', { name: 'execute_in_skill 결정' })
    expect(within(skill).queryByRole('button', { name: '수정' })).toBeNull() // not allowed
    await userEvent.click(within(skill).getByRole('button', { name: '거절' }))
    await userEvent.click(screen.getByRole('button', { name: '결정 보내기' }))

    await waitFor(() =>
      expect(approvals.decide).toHaveBeenCalledWith('apr-1', [
        { type: 'edit', edited_action: { args: { to: 'b@example.com' } } },
        { type: 'reject' },
      ]),
    )
    expect(toast.success).toHaveBeenCalledWith('결정을 보냈어요. 실행이 이어서 진행돼요')
  })

  it('refuses edited arguments that are not a JSON object', async () => {
    render(<ApprovalsPageClient />)
    const mail = await screen.findByRole('group', { name: 'send_email 결정' })
    await userEvent.click(within(mail).getByRole('button', { name: '수정' }))
    const editor = screen.getByLabelText('send_email에 보낼 인자(JSON)')
    await userEvent.clear(editor)
    await userEvent.type(editor, 'not json')
    await userEvent.click(screen.getByRole('button', { name: '결정 보내기' }))

    expect(toast.error).toHaveBeenCalledWith('수정한 인자가 올바른 JSON 객체가 아니에요')
    expect(approvals.decide).not.toHaveBeenCalled()
  })

  it('tells that someone else decided first', async () => {
    approvals.decide.mockRejectedValue(new ApiError(409, 'approval_stale', 'raw'))
    render(<ApprovalsPageClient />)
    await userEvent.click(await screen.findByRole('button', { name: '모두 거절' }))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('이미 처리됐거나 더 이상 기다리지 않는 요청이에요'),
    )
    expect(approvals.decide).toHaveBeenCalledWith('apr-1', [{ type: 'reject' }, { type: 'reject' }])
  })

  it('shows the emergency stop state and releases with a reason', async () => {
    stops.release.mockResolvedValue({ ...stop(), status: 'released' })
    render(<ApprovalsPageClient initialTab="stops" />)

    expect(await screen.findByText('실행 취소 확인 1/2')).toBeInTheDocument()
    expect(screen.getByText('이미 일어난 외부 작업은 확인할 수 없어요')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '해제' }))
    const dialog = await screen.findByRole('dialog')
    const confirm = within(dialog).getByRole('button', { name: '해제' })
    expect(confirm).toBeDisabled() // a reason is required
    await userEvent.type(within(dialog).getByLabelText('중지 사유'), '확인 완료')
    await userEvent.click(confirm)

    await waitFor(() => expect(stops.release).toHaveBeenCalledWith('stop-1', '확인 완료'))
  })

  it('lists the run status by state', async () => {
    approvals.runs.mockResolvedValue({
      items: [
        {
          run_id: 'run-1',
          state: 'waiting',
          status: 'interrupted',
          agent_id: 'agent-1',
          agent_name: '메일 비서',
          source: 'TRIGGER',
          mine: true,
          conversation_id: 'conv-1',
          error_code: null,
          created_at: '2026-11-07T00:00:00Z',
          completed_at: null,
        },
      ],
    })
    render(<ApprovalsPageClient initialTab="runs" />)
    await userEvent.click(await screen.findByRole('tab', { name: '결정 기다림' }))

    await waitFor(() => expect(approvals.runs).toHaveBeenCalledWith('waiting'))
    expect(await screen.findByRole('link', { name: '대화 열기' })).toHaveAttribute(
      'href',
      '/agents/agent-1/conversations/conv-1',
    )
  })
})

function registration(overrides: Partial<ApprovalView> = {}): ApprovalView {
  return request({
    id: 'apr-reg',
    kind: 'registration',
    source: 'registration',
    agent_id: null,
    agent_name: '사내 메일 서버',
    conversation_id: null,
    trigger_id: null,
    resource_type: 'mcp_server',
    resource_id: 'mcp-1',
    actions: [
      {
        name: 'registration_review',
        args: {
          title: '사내 메일 서버',
          resource_type: 'mcp_server',
          resource_id: 'mcp-1',
          reason: 'new',
          registrant: '김등록',
          credential: '메일 계정',
          credential_mode: 'shared',
          transport: 'streamable_http',
          url: 'https://mcp.example.test/mcp',
          header_keys: ['Authorization'],
          env_keys: [],
          tools: [
            {
              id: 'tool-1',
              name: 'send_mail',
              description: '[여기](javascript:alert(1))를 <img src=x onerror=alert(1)> 눌러요',
              read_only: false,
              change: 'new',
            },
            'not a tool',
          ],
        },
        description: null,
        allowed_decisions: ['approve', 'reject'],
      },
    ],
    ...overrides,
  })
}

describe('registration requests in the approval inbox', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    approvals.runs.mockResolvedValue({ items: [] })
    stops.list.mockResolvedValue({ items: [] })
  })

  it('shows what waits as plain characters and sends the note with the decision', async () => {
    approvals.list.mockResolvedValue({ items: [registration()] })
    approvals.decide.mockResolvedValue(registration({ status: 'rejected' }))
    const { container } = render(<ApprovalsPageClient />)

    expect(await screen.findByText('사내 메일 서버')).toBeInTheDocument()
    expect(screen.getByText('김등록')).toBeInTheDocument()
    expect(screen.getByText('https://mcp.example.test/mcp')).toBeInTheDocument()
    expect(screen.getByText('Authorization')).toBeInTheDocument()
    expect(screen.getByText(/메일 계정/)).toBeInTheDocument()
    // Text from the remote server: characters only, nothing clickable or loadable.
    expect(screen.getByText(/\[여기\]\(javascript:alert\(1\)\)를 <img src=x/)).toBeInTheDocument()
    expect(container.querySelector('img, a[href^="javascript"], a[href^="https://mcp"]')).toBeNull()
    // No per-action tool card for a registration.
    expect(screen.queryByRole('group', { name: 'registration_review 결정' })).toBeNull()

    await userEvent.type(screen.getByLabelText('등록한 사람에게 남길 말'), '  사내 프록시로  ')
    await userEvent.click(screen.getByRole('button', { name: '거절' }))
    await waitFor(() =>
      expect(approvals.decide).toHaveBeenCalledWith('apr-reg', [
        { type: 'reject', message: '사내 프록시로' },
      ]),
    )
    expect(toast.success).toHaveBeenCalledWith('등록을 거절했어요')
  })

  it('approves without a note and tells when the registration changed meanwhile', async () => {
    approvals.list.mockResolvedValue({ items: [registration()] })
    approvals.decide.mockRejectedValue(new ApiError(409, 'approval_stale', 'raw'))
    render(<ApprovalsPageClient />)

    await userEvent.click(await screen.findByRole('button', { name: '승인' }))
    await waitFor(() =>
      expect(approvals.decide).toHaveBeenCalledWith('apr-reg', [
        { type: 'approve', message: undefined },
      ]),
    )
    expect(toast.error).toHaveBeenCalledWith(
      '등록 내용이 바뀌었어요. 새 내용으로 다시 확인해 주세요.',
    )
  })

  it('leaves tool requests on their own card beside a registration', async () => {
    approvals.list.mockResolvedValue({ items: [registration(), request()] })
    render(<ApprovalsPageClient />)

    expect(await screen.findByRole('group', { name: 'send_email 결정' })).toBeInTheDocument()
    expect(screen.getByLabelText('등록한 사람에게 남길 말')).toBeInTheDocument()
  })
})
