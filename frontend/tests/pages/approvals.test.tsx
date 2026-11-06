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
