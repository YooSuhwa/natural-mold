import { WorkflowToolCard, isWorkflowToolName } from '@/components/chat/tool-ui/workflow-tool-ui'
import { AgentWorkflowTools } from '@/features/workflows/components/agent-workflow-tools'
import type { WorkflowRunDetail } from '@/lib/types/workflow'
import { render, screen, userEvent } from '../../test-utils'

const state = vi.hoisted(() => ({
  run: undefined as unknown,
  cancel: vi.fn(),
  linked: [] as unknown[],
  workflows: [] as unknown[],
  save: vi.fn(),
}))

vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock('@/components/chat/conversation-context', () => ({ useChatConversationId: () => 'conv-1' }))
vi.mock('@/features/workflows/components/run-approval-card', () => ({
  RunApprovalCard: () => <div>approval-card</div>,
}))
vi.mock('@/lib/hooks/use-workflows', () => ({
  useToolCallWorkflowRun: () => ({ data: state.run }),
  useWorkflow: () => ({ data: { name: '고객 문의 처리' } }),
  useCancelWorkflowRun: () => ({ isPending: false, mutate: state.cancel }),
  useAgentWorkflowTools: () => ({ isPending: false, data: state.linked }),
  useWorkflows: () => ({ data: state.workflows }),
  useSetAgentWorkflowTools: () => ({ isPending: false, mutate: state.save }),
}))

function run(status: WorkflowRunDetail['status'], output: unknown = null): WorkflowRunDetail {
  return {
    id: 'run-1',
    workflow_id: 'wf-1',
    version_id: 'v-1',
    version_no: 1,
    status,
    error_code: null,
    error_message: null,
    current_node: null,
    created_at: '2026-11-09T10:00:00',
    started_at: null,
    finished_at: null,
    input: {},
    output,
    steps: [],
  } as unknown as WorkflowRunDetail
}

describe('the workflow chat tool card', () => {
  beforeEach(() => state.cancel.mockReset())

  it('recognises workflow tools by name', () => {
    expect(isWorkflowToolName('workflow_14db7cde0ddd')).toBe(true)
    expect(isWorkflowToolName('web_search')).toBe(false)
  })

  it('shows a waiting run with its approval, a link and a cancel', async () => {
    const user = userEvent.setup()
    state.run = run('waiting')
    render(<WorkflowToolCard toolCallId="call-1" />)
    const card = screen.getByRole('region', { name: '워크플로 고객 문의 처리' })
    expect(card).toHaveTextContent('승인 대기')
    expect(screen.getByText('approval-card')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /실행 상세/ })).toHaveAttribute(
      'href',
      '/workflows/wf-1/runs/run-1',
    )
    await user.click(screen.getByRole('button', { name: '워크플로 취소' }))
    expect(state.cancel).toHaveBeenCalled()
  })

  it('shows the output of a finished run and no cancel', () => {
    state.run = run('completed', { answer: '환불 처리했습니다' })
    render(<WorkflowToolCard toolCallId="call-1" />)
    expect(screen.getByText(/환불 처리했습니다/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '워크플로 취소' })).not.toBeInTheDocument()
  })

  it('says why when the run could not start', () => {
    state.run = undefined
    render(
      <WorkflowToolCard
        toolCallId="call-1"
        result={JSON.stringify({ status: 'failed', error: '게시한 버전이 없습니다' })}
      />,
    )
    expect(
      screen.getByText('워크플로를 시작하지 못했어요: 게시한 버전이 없습니다'),
    ).toBeInTheDocument()
  })
})

describe('the agent workflow tools panel', () => {
  beforeEach(() => state.save.mockReset())

  it('adds only published, unlinked workflows and removes one', async () => {
    const user = userEvent.setup()
    state.linked = [
      {
        workflow_id: 'wf-1',
        name: '고객 문의 처리',
        description: null,
        published_version_no: 1,
        usable: true,
        broken_reason: null,
      },
      {
        workflow_id: 'wf-old',
        name: '옛 흐름',
        description: null,
        published_version_no: 1,
        usable: false,
        broken_reason: 'workflow_archived',
      },
    ]
    state.workflows = [
      { id: 'wf-1', name: '고객 문의 처리', published_version_no: 1, archived_at: null },
      { id: 'wf-2', name: '주간 보고', published_version_no: 2, archived_at: null },
      { id: 'wf-3', name: '초안만', published_version_no: null, archived_at: null },
    ]
    render(<AgentWorkflowTools agentId="agent-1" />)
    expect(screen.getByText('· 보관됨')).toBeInTheDocument()
    const select = screen.getByLabelText('붙일 워크플로') as HTMLSelectElement
    expect([...select.options].map((option) => option.value)).toEqual(['', 'wf-2'])
    await user.selectOptions(select, 'wf-2')
    await user.click(screen.getByRole('button', { name: '붙이기' }))
    expect(state.save.mock.calls[0][0]).toEqual(['wf-1', 'wf-old', 'wf-2'])
    await user.click(screen.getByRole('button', { name: '옛 흐름 빼기' }))
    expect(state.save.mock.calls[1][0]).toEqual(['wf-1'])
  })
})
