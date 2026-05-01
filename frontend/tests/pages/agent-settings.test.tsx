import { render, screen, userEvent } from '../test-utils'
import AgentSettingsPage from '@/app/agents/[agentId]/settings/page'
import { mockAgent, mockToolList, mockTriggerList } from '../mocks/fixtures'

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

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}))

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

// Mock React.use() for params Promise
vi.mock('react', async () => {
  const actual = await vi.importActual('react')
  return {
    ...actual,
    use: (value: unknown) => {
      if (value && typeof value === 'object' && 'agentId' in value) return value
      return (actual as Record<string, unknown>).use(value)
    },
  }
})

const mockUseAgent = vi.fn()

const mockUpdateAgent = vi.fn().mockResolvedValue({})
const mockDeleteAgent = vi.fn().mockResolvedValue({})

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgents: () => ({ data: [], isLoading: false }),
  useAgent: (...args: unknown[]) => mockUseAgent(...args),
  useCreateAgent: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateAgent: () => ({ mutateAsync: mockUpdateAgent, isPending: false }),
  useDeleteAgent: () => ({ mutateAsync: mockDeleteAgent, isPending: false }),
  useToggleFavorite: () => ({ mutate: vi.fn() }),
  useGenerateAgentImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

const mockUseTools = vi.fn()

vi.mock('@/lib/hooks/use-tools', () => ({
  useTools: () => mockUseTools(),
}))

vi.mock('@/lib/hooks/use-skills', () => ({
  useSkills: () => ({ data: [] }),
}))

vi.mock('@/lib/hooks/use-middlewares', () => ({
  useMiddlewares: () => ({ data: [] }),
}))

const mockCreateTrigger = vi.fn().mockResolvedValue({})
const mockUpdateTrigger = vi.fn()
const mockDeleteTrigger = vi.fn()

vi.mock('@/lib/hooks/use-triggers', () => ({
  useTriggers: (...args: unknown[]) => mockUseTriggers(...args),
  useCreateTrigger: () => ({ mutateAsync: mockCreateTrigger, isPending: false }),
  useUpdateTrigger: () => ({ mutate: mockUpdateTrigger }),
  useDeleteTrigger: () => ({ mutate: mockDeleteTrigger, isPending: false }),
}))

const mockUseTriggers = vi.fn()

// Mock ModelSelect to avoid complex providers dependency
vi.mock('@/components/model/model-select', () => ({
  ModelSelect: ({ value, onValueChange }: { value: string; onValueChange: (v: string) => void }) => (
    <select data-testid="model-select" value={value} onChange={(e) => onValueChange(e.target.value)}>
      <option value="model-1">GPT-4o</option>
      <option value="model-2">Claude Sonnet 4</option>
    </select>
  ),
}))

// Mock AssistantPanel
vi.mock('@/components/agent/assistant-panel', () => ({
  AssistantPanel: () => <div data-testid="assistant-panel" />,
}))

// Mock complex UI components
vi.mock('@/components/ui/alert-dialog', () => ({
  AlertDialog: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertDialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertDialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertDialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  AlertDialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  AlertDialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertDialogAction: ({
    children,
    onClick,
  }: {
    children: React.ReactNode
    onClick?: () => void
    className?: string
  }) => <button onClick={onClick}>{children}</button>,
  AlertDialogCancel: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
  AlertDialogTrigger: ({ render }: { render: React.ReactNode }) => <>{render}</>,
}))

vi.mock('@/components/shared/delete-confirm-dialog', () => ({
  DeleteConfirmDialog: ({
    open,
    onConfirm,
    onOpenChange,
  }: {
    open: boolean
    onConfirm: () => void
    onOpenChange: (v: boolean) => void
    title?: string
    description?: string
    cancelLabel?: string
    confirmLabel?: string
    isPending?: boolean
  }) =>
    open ? (
      <div data-testid="delete-confirm-dialog">
        <button onClick={onConfirm}>확인 삭제</button>
        <button onClick={() => onOpenChange(false)}>취소</button>
      </div>
    ) : null,
}))

// Mock Slider component
vi.mock('@/components/ui/slider', () => ({
  Slider: () => <div data-testid="slider" />,
}))

// Agent fixture with new required fields
const fullAgent = {
  ...mockAgent,
  skills: [],
  is_favorite: false,
  model_params: null,
  middleware_configs: [],
}

/**
 * 페이지 구조 (M10 이후): form/visual 두 탭 + 좌측은 form/visual 토글, 우측은
 * AssistantPanel. 옛 테스트의 "모델/도구·스킬/트리거" 탭 단위 검증은 모두
 * FormMode 내부 구현으로 흡수되어 페이지 단위로는 의미가 없어졌다.
 *
 * 페이지 단위 테스트는 다음만 책임진다:
 *   - 로딩 스켈레톤
 *   - 헤더 컨트롤(이름/설명 입력, save/delete 버튼, back 버튼)
 *   - form/visual 탭 존재
 *   - save 클릭 시 updateAgent 호출
 *
 * FormMode 내부의 detail 시나리오는 form-mode 컴포넌트 단위 테스트와 e2e가
 * 책임진다.
 */
describe('AgentSettingsPage', () => {
  beforeEach(() => {
    mockUseAgent.mockReturnValue({ data: undefined, isLoading: false })
    mockUseTools.mockReturnValue({ data: undefined })
    mockUseTriggers.mockReturnValue({ data: undefined })
  })

  it('shows loading skeleton when agent is loading', () => {
    mockUseAgent.mockReturnValue({ data: undefined, isLoading: true })
    const { container } = render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it('renders header with name + description inputs filled from agent data', () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    expect(screen.getByDisplayValue('Test Agent')).toBeInTheDocument()
    expect(screen.getByDisplayValue('A test agent')).toBeInTheDocument()
  })

  it('renders form / visual tabs', () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    // ``tabs.form`` = "폼", ``tabs.visual`` = "비주얼"
    expect(screen.getByRole('tab', { name: /폼/ })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /비주얼/ })).toBeInTheDocument()
  })

  it('renders save + delete + back buttons in header', () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    expect(screen.getByLabelText('돌아가기')).toBeInTheDocument()
    expect(screen.getByLabelText('에이전트 삭제')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '저장' })).toBeInTheDocument()
  })

  it('right pane mounts the AssistantPanel', () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    expect(screen.getByTestId('assistant-panel')).toBeInTheDocument()
  })

  it('calls updateAgent when save is clicked after editing name', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    const nameInput = screen.getByDisplayValue('Test Agent')
    await user.clear(nameInput)
    await user.type(nameInput, 'Updated Agent')
    await user.click(screen.getByRole('button', { name: '저장' }))
    expect(mockUpdateAgent).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Updated Agent' }),
    )
  })

  // FormMode 내부 동작(모델 셀렉트, 도구 체크박스 토글, 트리거 CRUD 등)은
  // form-mode 컴포넌트 단위 테스트와 e2e/smoke로 분리된다. 페이지 단위에서는
  // 다시 다루지 않는다.
  it.skip('legacy: renders agent name input when agent loaded', () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    expect(screen.getByText('이름')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Test Agent')).toBeInTheDocument()
  })

  it.skip('renders system prompt textarea', () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    expect(screen.getByText('시스템 프롬프트')).toBeInTheDocument()
    expect(screen.getByDisplayValue('You are a helpful assistant.')).toBeInTheDocument()
  })

  it.skip('renders model selector in model tab', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    // Click "모델" tab
    await user.click(screen.getByRole('tab', { name: '모델' }))
    // Model select should be present
    expect(screen.getByTestId('model-select')).toBeInTheDocument()
  })

  it.skip('renders tool checkboxes in tools tab', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    // Click tools tab
    await user.click(screen.getByRole('tab', { name: '도구·스킬' }))
    expect(screen.getByText('연결된 도구')).toBeInTheDocument()
    const checkboxes = screen.getAllByRole('checkbox')
    expect(checkboxes.length).toBeGreaterThanOrEqual(mockToolList.length)
  })

  it.skip('shows save and delete buttons', () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    expect(screen.getByText('저장')).toBeInTheDocument()
    expect(screen.getByText('에이전트 삭제')).toBeInTheDocument()
  })

  it.skip('shows trigger section in triggers tab', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: mockTriggerList })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    await user.click(screen.getByRole('tab', { name: '트리거' }))
    expect(screen.getByText('활성')).toBeInTheDocument()
  })

  it.skip('shows empty tool message when no tools exist', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: [] })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    await user.click(screen.getByRole('tab', { name: '도구·스킬' }))
    expect(screen.getByText(/등록된 도구가 없습니다/)).toBeInTheDocument()
  })

  it.skip('renders description input with agent description', () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    expect(screen.getByText('설명')).toBeInTheDocument()
    expect(screen.getByDisplayValue('A test agent')).toBeInTheDocument()
  })

  it.skip('shows back button', () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    expect(screen.getByText('돌아가기')).toBeInTheDocument()
  })

  it.skip('renders add trigger button in triggers tab', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    await user.click(screen.getByRole('tab', { name: '트리거' }))
    expect(screen.getByText('자동 실행 추가')).toBeInTheDocument()
  })

  it.skip('submits trigger form when filled and clicked', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    // Switch to triggers tab
    await user.click(screen.getByRole('tab', { name: '트리거' }))
    // Open form
    await user.click(screen.getByText('자동 실행 추가'))
    // Fill interval
    const intervalInput = screen.getByPlaceholderText('10')
    await user.clear(intervalInput)
    await user.type(intervalInput, '30')
    // Fill message
    const messageInput = screen.getByPlaceholderText(
      '예: 한글과컴퓨터 최신 뉴스를 검색하고 요약해줘',
    )
    await user.type(messageInput, '테스트 메시지')
    // Submit
    await user.click(screen.getByText('트리거 추가'))
    expect(mockCreateTrigger).toHaveBeenCalledWith(
      expect.objectContaining({
        trigger_type: 'interval',
        input_message: '테스트 메시지',
      }),
    )
  })

  it.skip('cancels trigger form and closes it', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    await user.click(screen.getByRole('tab', { name: '트리거' }))
    await user.click(screen.getByText('자동 실행 추가'))
    // Verify form is open
    expect(screen.getByText('실행 간격 (분)')).toBeInTheDocument()
    expect(screen.getByText('실행 시 보낼 메시지')).toBeInTheDocument()
    // Click cancel — find the cancel button inside the trigger form
    // The form cancel is a <button> element within the form, alongside "트리거 추가"
    const addButton = screen.getByText('트리거 추가')
    const formContainer = addButton.closest('.flex.gap-2')!
    const cancelButton = formContainer.querySelector('button:last-child')!
    await user.click(cancelButton)
    // Form should be closed — "실행 간격 (분)" should no longer be visible
    expect(screen.queryByText('실행 간격 (분)')).not.toBeInTheDocument()
  })

  it.skip('shows trigger form when add trigger is clicked', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    await user.click(screen.getByRole('tab', { name: '트리거' }))
    await user.click(screen.getByText('자동 실행 추가'))
    expect(screen.getByText('실행 간격 (분)')).toBeInTheDocument()
    expect(screen.getByText('실행 시 보낼 메시지')).toBeInTheDocument()
    expect(screen.getByText('트리거 추가')).toBeInTheDocument()
    // '취소' appears in both trigger form and AlertDialog mock
    expect(screen.getAllByText('취소').length).toBeGreaterThanOrEqual(1)
  })

  it.skip('renders page title with agent name', () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    expect(screen.getByText('에이전트 설정: Test Agent')).toBeInTheDocument()
  })

  it.skip('calls updateAgent when save button clicked', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    // Modify something to make dirty
    const nameInput = screen.getByDisplayValue('Test Agent')
    await user.clear(nameInput)
    await user.type(nameInput, 'Updated Agent')
    await user.click(screen.getByText('저장'))
    expect(mockUpdateAgent).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Updated Agent',
        system_prompt: 'You are a helpful assistant.',
      }),
    )
  })

  it.skip('toggles tool checkbox in tools tab', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    await user.click(screen.getByRole('tab', { name: '도구·스킬' }))
    const checkboxes = screen.getAllByRole('checkbox')
    // First checkbox should be checked (agent has tool-1)
    expect(checkboxes[0]).toBeChecked()
    // Toggle it off
    await user.click(checkboxes[0])
    expect(checkboxes[0]).not.toBeChecked()
    // Toggle it back on
    await user.click(checkboxes[0])
    expect(checkboxes[0]).toBeChecked()
  })

  it.skip('shows trigger with pause/resume button', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: mockTriggerList })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    await user.click(screen.getByRole('tab', { name: '트리거' }))
    // Active trigger should have a pause button
    expect(screen.getByLabelText('일시정지')).toBeInTheDocument()
    // Paused trigger should have a resume button
    expect(screen.getByLabelText('재개')).toBeInTheDocument()
    // Delete button
    expect(screen.getAllByLabelText('트리거 삭제').length).toBe(2)
  })

  it.skip('calls updateTrigger when pause button clicked', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: mockTriggerList })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    await user.click(screen.getByRole('tab', { name: '트리거' }))
    await user.click(screen.getByLabelText('일시정지'))
    expect(mockUpdateTrigger).toHaveBeenCalledWith({
      triggerId: 'trigger-1',
      data: { status: 'paused' },
    })
  })

  it.skip('deletes trigger via DeleteConfirmDialog', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: mockTriggerList })
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    await user.click(screen.getByRole('tab', { name: '트리거' }))
    // Click delete on the first trigger
    const deleteButtons = screen.getAllByLabelText('트리거 삭제')
    await user.click(deleteButtons[0])
    // DeleteConfirmDialog should appear
    expect(screen.getByTestId('delete-confirm-dialog')).toBeInTheDocument()
    // Confirm deletion
    await user.click(screen.getByText('확인 삭제'))
    expect(mockDeleteTrigger).toHaveBeenCalledWith('trigger-1')
  })

  it.skip('shows model skeleton when model select is loading', () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )
    // Page renders without skeletons when agent is loaded
    expect(screen.getByText('에이전트 설정: Test Agent')).toBeInTheDocument()
  })
})
