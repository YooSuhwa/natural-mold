import { RunDetailClient } from '@/app/workflows/[workflowId]/runs/[runId]/_components/run-detail-client'
import type { WorkflowRunDetail } from '@/lib/types/workflow'
import { render, screen, userEvent } from '../../test-utils'

const state = vi.hoisted(() => ({
  run: undefined as unknown,
  requests: [] as unknown[],
  decide: vi.fn(),
}))

vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock('@/lib/hooks/use-workflows', () => ({
  useWorkflowRun: () => ({ isPending: false, isError: false, data: state.run }),
  useWorkflow: () => ({ data: { name: '고객 문의 처리' } }),
  useWorkflowVersion: () => ({ data: undefined }),
  useCancelWorkflowRun: () => ({ isPending: false, mutate: vi.fn() }),
}))
vi.mock('@/lib/hooks/use-approvals', () => ({
  useWorkflowRunApproval: (runId: string, _status: string, _node: string | null, on: boolean) => ({
    data: on
      ? ((state.requests as { workflow_run_id: string }[]).find(
          (item) => item.workflow_run_id === runId,
        ) ?? null)
      : undefined,
  }),
  useDecideApproval: () => ({ isPending: false, mutate: state.decide }),
}))

function run(status: WorkflowRunDetail['status']): WorkflowRunDetail {
  return {
    id: 'run-1',
    workflow_id: 'wf-1',
    version_id: 'v-1',
    version_no: 1,
    status,
    current_node: 'classify',
    error_code: status === 'unknown' ? 'step_outcome_unknown' : null,
    error_message: null,
    created_at: '2026-11-08T10:00:00',
    started_at: null,
    finished_at: null,
    input: { inquiry: '환불' },
    output: null,
    steps: [],
  } as unknown as WorkflowRunDetail
}

function request(name: string) {
  return {
    id: 'req-1',
    kind: 'business',
    workflow_run_id: 'run-1',
    actions: [{ name, args: { title: '결과를 알 수 없는 단계: 문의 분류', body: '확인하세요' } }],
  }
}

describe('RunDetailClient approval card', () => {
  beforeEach(() => state.decide.mockReset())

  it('asks to run an unknown step again or end the run', async () => {
    state.run = run('unknown')
    state.requests = [request('workflow_unknown_check')]
    render(<RunDetailClient workflowId="wf-1" runId="run-1" />)
    expect(screen.getByText('결과를 알 수 없는 단계: 문의 분류')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '다시 실행하고 계속' }))
    expect(state.decide.mock.calls[0][0]).toEqual({ id: 'req-1', decisions: [{ type: 'approve' }] })
    await userEvent.click(screen.getByRole('button', { name: '실패로 끝내기' }))
    expect(state.decide.mock.calls[1][0]).toEqual({ id: 'req-1', decisions: [{ type: 'reject' }] })
  })

  it('keeps the approval-node wording for an approval node', () => {
    state.run = run('waiting')
    state.requests = [request('workflow_approval')]
    render(<RunDetailClient workflowId="wf-1" runId="run-1" />)
    expect(screen.getByRole('button', { name: '승인 후 계속' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '다시 실행하고 계속' })).toBeNull()
  })
})

describe('RunDetailClient loop rounds', () => {
  it('lists each round of a looping step separately', async () => {
    const step = (position: number, round: number, branch: string | null) => ({
      id: `s-${position}`,
      position,
      node_id: position % 2 ? 'draft' : 'passed',
      node_type: position % 2 ? 'agent' : 'decision_bool',
      round,
      status: 'completed',
      branch,
      output: null,
      error_code: null,
      error_message: null,
      conversation_id: null,
      started_at: null,
      finished_at: null,
    })
    state.run = {
      ...run('completed'),
      steps: [step(1, 1, null), step(2, 1, 'false'), step(3, 2, null), step(4, 2, 'limit')],
    }
    state.requests = []
    render(<RunDetailClient workflowId="wf-1" runId="run-1" />)
    const timeline = screen.getByRole('list', { name: '단계' })
    expect(timeline.querySelectorAll('li')).toHaveLength(4)
    expect(screen.getAllByText('2회차')).toHaveLength(2)
    expect(screen.getByText('한도 초과')).toBeInTheDocument()
  })
})

describe('RunDetailClient nodes plan', () => {
  const base = {
    round: 1,
    status: 'completed' as const,
    branch: null,
    output: null,
    error_code: null,
    error_message: null,
    conversation_id: null,
    started_at: null,
    finished_at: null,
  }

  it('lists the children of a for_each step under it, by item', () => {
    state.run = {
      ...run('completed'),
      steps: [
        { ...base, id: 'p', position: 1, node_id: 'each', node_type: 'for_each' },
        ...[0, 1].map((n) => ({
          ...base,
          id: `c${n}`,
          position: 1,
          node_id: `each.${n}`,
          node_type: 'agent' as const,
          parent_step_id: 'p',
          branch_key: String(n),
        })),
        { ...base, id: 'r', position: 2, node_id: 'route', node_type: 'switch', branch: 'news' },
      ],
    }
    state.requests = []
    render(<RunDetailClient workflowId="wf-1" runId="run-1" />)
    const children = screen.getByRole('list', { name: '하위 실행' })
    expect(children.querySelectorAll('li')).toHaveLength(2)
    expect(screen.getByText('항목 1')).toBeInTheDocument()
    expect(screen.getByText('항목 2')).toBeInTheDocument()
    expect(screen.getByText('news')).toBeInTheDocument() // a switch case keeps its own name
  })

  it('answers an ask step with written text', async () => {
    state.decide.mockReset()
    state.run = run('waiting')
    state.requests = [
      {
        id: 'req-2',
        kind: 'business',
        workflow_run_id: 'run-1',
        actions: [{ name: 'workflow_ask', args: { title: '확인 질문', body: '어느 지역인가요?' } }],
      },
    ]
    render(<RunDetailClient workflowId="wf-1" runId="run-1" />)
    expect(screen.getByText('어느 지역인가요?')).toBeInTheDocument()
    const send = screen.getByRole('button', { name: '답 보내기' })
    expect(send).toBeDisabled()
    await userEvent.type(screen.getByRole('textbox', { name: '답' }), '서울')
    await userEvent.click(send)
    expect(state.decide.mock.calls[0][0]).toEqual({
      id: 'req-2',
      decisions: [{ type: 'approve', message: '서울' }],
    })
    expect(screen.queryByRole('button', { name: '거절' })).toBeNull()
  })
})
