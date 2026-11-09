import { AiEditPanel } from '@/features/workflows/components/ai-edit-panel'
import type { WorkflowAssistResponse } from '@/lib/types/workflow'
import { render, screen, userEvent } from '../../test-utils'

const state = vi.hoisted(() => ({ mutate: vi.fn(), pending: false }))

vi.mock('@/lib/hooks/use-workflows', () => ({
  useWorkflowAssist: () => ({ isPending: state.pending, mutate: state.mutate }),
}))

function reply(kind: WorkflowAssistResponse['kind'], message: string): WorkflowAssistResponse {
  return {
    kind,
    message,
    plan: kind === 'proposal' ? ['조사 단계 추가'] : [],
    definition:
      kind === 'proposal'
        ? { schema_version: 'moldy.workflow.v1', inputs: [], nodes: [], edges: [] }
        : null,
    issues: [],
    base_revision: 4,
  }
}

function answerWith(response: WorkflowAssistResponse) {
  state.mutate.mockImplementation(
    (_body: unknown, options: { onSuccess: (r: WorkflowAssistResponse) => void }) =>
      options.onSuccess(response),
  )
}

describe('AiEditPanel', () => {
  beforeEach(() => {
    state.mutate.mockReset()
    state.pending = false
  })

  it('sends the request with the draft revision and earlier turns, and shows a question', async () => {
    const user = userEvent.setup()
    const onProposal = vi.fn()
    answerWith(reply('question', '어떤 자료를 다루나요?'))
    render(
      <AiEditPanel
        workflowId="wf-1"
        revision={4}
        canSend
        proposal={null}
        onProposal={onProposal}
        onReview={vi.fn()}
      />,
    )
    await user.type(screen.getByLabelText('AI에게 요청'), '흐름 만들어 줘')
    await user.click(screen.getByRole('button', { name: '보내기' }))
    expect(state.mutate.mock.calls[0][0]).toEqual({
      revision: 4,
      message: '흐름 만들어 줘',
      history: [],
    })
    expect(screen.getByText('어떤 자료를 다루나요?')).toBeInTheDocument()
    expect(onProposal).not.toHaveBeenCalled()

    // The next request carries the conversation so far.
    await user.type(screen.getByLabelText('AI에게 요청'), '논문')
    await user.click(screen.getByRole('button', { name: '보내기' }))
    expect(state.mutate.mock.calls[1][0].history).toEqual([
      { role: 'user', text: '흐름 만들어 줘' },
      { role: 'assistant', text: '어떤 자료를 다루나요?' },
    ])
  })

  it('hands a proposal to the editor', async () => {
    const user = userEvent.setup()
    const onProposal = vi.fn()
    const response = reply('proposal', '조사 흐름을 만듭니다.')
    answerWith(response)
    render(
      <AiEditPanel
        workflowId="wf-1"
        revision={4}
        canSend
        proposal={null}
        onProposal={onProposal}
        onReview={vi.fn()}
      />,
    )
    await user.type(screen.getByLabelText('AI에게 요청'), '조사 흐름')
    await user.click(screen.getByRole('button', { name: '보내기' }))
    expect(onProposal).toHaveBeenCalledWith(response)
  })

  it('shows the plan of a large change before the diff, and holds new requests meanwhile', async () => {
    const user = userEvent.setup()
    const onReview = vi.fn()
    render(
      <AiEditPanel
        workflowId="wf-1"
        revision={4}
        canSend
        proposal={{ response: reply('proposal', '조사 흐름'), reviewing: false }}
        onProposal={vi.fn()}
        onReview={onReview}
      />,
    )
    expect(screen.getByRole('region', { name: '이렇게 바꿀게요' })).toHaveTextContent(
      '조사 단계 추가',
    )
    expect(screen.getByLabelText('AI에게 요청')).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '바뀌는 점 보기' }))
    expect(onReview).toHaveBeenCalled()
  })

  it('waits for the draft to be saved before sending', () => {
    render(
      <AiEditPanel
        workflowId="wf-1"
        revision={4}
        canSend={false}
        proposal={null}
        onProposal={vi.fn()}
        onReview={vi.fn()}
      />,
    )
    expect(screen.getByText('저장이 끝나면 보낼 수 있어요.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '보내기' })).toBeDisabled()
  })
})
