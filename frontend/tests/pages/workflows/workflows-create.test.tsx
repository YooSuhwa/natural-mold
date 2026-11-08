import { WorkflowsPageClient } from '@/app/workflows/_components/workflows-page-client'
import { render, screen, userEvent } from '../../test-utils'

const state = vi.hoisted(() => ({ create: vi.fn(), push: vi.fn() }))

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: state.push }) }))
vi.mock('@/lib/hooks/use-workflows', () => ({
  useWorkflows: () => ({ isPending: false, isError: false, data: [], refetch: vi.fn() }),
  useCreateWorkflow: () => ({ isPending: false, mutate: state.create }),
}))
vi.mock('@/lib/hooks/use-agents', () => ({
  useAgents: () => ({
    data: [
      { id: 'agent-a', name: '분류 담당' },
      { id: 'agent-b', name: '답변 담당' },
    ],
  }),
}))

describe('creating a workflow', () => {
  beforeEach(() => state.create.mockReset())

  it('starts the inquiry template only once both agents are chosen', async () => {
    const user = userEvent.setup()
    render(<WorkflowsPageClient />)
    await user.click(screen.getByRole('button', { name: '새 워크플로' }))
    await user.type(screen.getByLabelText('이름'), '고객 문의')
    await user.click(screen.getByRole('radio', { name: '고객 문의 답변 초안' }))
    const submit = screen.getByRole('button', { name: '만들기' })
    expect(submit).toBeDisabled()

    await user.selectOptions(screen.getByLabelText('문의를 분류할 에이전트'), 'agent-a')
    expect(submit).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('답변을 쓸 에이전트'), 'agent-b')
    await user.click(submit)

    const [{ name, definition }] = state.create.mock.calls[0]
    expect(name).toBe('고객 문의')
    const agents = definition.nodes.filter((node: { type: string }) => node.type === 'agent')
    expect(agents.map((node: { agent_id: string }) => node.agent_id)).toEqual([
      'agent-a',
      'agent-b',
    ])
    expect(agents[0].label).toBe('문의 분류')
  })

  it('a blank draft sends no definition', async () => {
    const user = userEvent.setup()
    render(<WorkflowsPageClient />)
    await user.click(screen.getByRole('button', { name: '새 워크플로' }))
    await user.type(screen.getByLabelText('이름'), '빈 흐름')
    await user.click(screen.getByRole('button', { name: '만들기' }))
    expect(state.create.mock.calls[0][0]).toEqual({ name: '빈 흐름', definition: undefined })
  })
})
