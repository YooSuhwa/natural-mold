import { render, screen, userEvent, waitFor } from '../../test-utils'
import { TriggersTab } from '@/app/agents/[agentId]/settings/_components/triggers-tab'
import { mockTriggerList } from '../../mocks/fixtures'

const mockCreateTrigger = vi.fn().mockResolvedValue({})
const mockUpdateTriggerAsync = vi.fn().mockResolvedValue({})
const mockUpdateTrigger = vi.fn()

vi.mock('@/lib/hooks/use-triggers', () => ({
  useTriggers: () => ({ data: mockTriggerList }),
  useCreateTrigger: () => ({ mutateAsync: mockCreateTrigger, isPending: false }),
  useUpdateTrigger: () => ({
    mutate: mockUpdateTrigger,
    mutateAsync: mockUpdateTriggerAsync,
    isPending: false,
  }),
}))

vi.mock('@/lib/hooks/use-conversations', () => ({
  useConversations: () => ({ data: [] }),
}))

vi.mock('next/link', () => ({
  default: ({
    children,
    href,
    ...props
  }: {
    children: React.ReactNode
    href: string
    [key: string]: unknown
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}))

describe('TriggersTab', () => {
  beforeEach(() => {
    mockCreateTrigger.mockClear()
    mockUpdateTrigger.mockClear()
    mockUpdateTriggerAsync.mockClear()
  })

  it('opens a dialog when adding a trigger instead of rendering an inline form', async () => {
    const user = userEvent.setup()
    render(<TriggersTab agentId="agent-1" onRequestDelete={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: '자동 실행 추가' }))

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '스케줄' })).toBeInTheDocument()
  })

  it('edits an existing trigger from the agent settings schedule tab', async () => {
    const user = userEvent.setup()
    render(<TriggersTab agentId="agent-1" onRequestDelete={vi.fn()} />)

    await user.click(screen.getAllByRole('button', { name: '수정' })[0])
    await user.clear(screen.getByPlaceholderText('예: 매일 아침 뉴스 요약'))
    await user.type(screen.getByPlaceholderText('예: 매일 아침 뉴스 요약'), '수정된 스케줄')
    await user.click(screen.getByRole('button', { name: '스케줄 수정' }))

    await waitFor(() => expect(mockUpdateTriggerAsync).toHaveBeenCalledTimes(1))
    expect(mockUpdateTriggerAsync).toHaveBeenCalledWith({
      triggerId: 'trigger-1',
      data: expect.objectContaining({ name: '수정된 스케줄' }),
    })
  })
})
