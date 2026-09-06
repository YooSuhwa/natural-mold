import { render, screen, userEvent, waitFor, within } from '../test-utils'
import AgentSettingsPage from '@/app/agents/[agentId]/settings/page'
import { SettingsPanel } from '@/app/agents/[agentId]/settings/_components/right-panel/settings-panel'
import { mockAgent, mockToolList } from '../mocks/fixtures'

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
const mockUseModels = vi.fn()
const mockRefetchAgent = vi.fn()

const mockUpdateAgent = vi.fn().mockResolvedValue({})
const mockDeleteAgent = vi.fn().mockResolvedValue({})
const mockUpdateAgentMemorySettings = vi.fn().mockResolvedValue({})

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgents: () => ({ data: [], isLoading: false }),
  useAgent: (...args: unknown[]) => mockUseAgent(...args),
  useCreateAgent: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateAgent: () => ({ mutateAsync: mockUpdateAgent, isPending: false }),
  useDeleteAgent: () => ({ mutateAsync: mockDeleteAgent, isPending: false }),
  useToggleFavorite: () => ({ mutate: vi.fn() }),
  useGenerateAgentImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

vi.mock('@/lib/hooks/use-models', () => ({
  useModels: () => mockUseModels(),
}))

const mockUseTools = vi.fn()
const mockUseAllMcpTools = vi.fn()

vi.mock('@/lib/hooks/use-tools', () => ({
  useTools: () => mockUseTools(),
}))

vi.mock('@/lib/hooks/use-skills', () => ({
  useSkills: () => ({ data: [] }),
}))

vi.mock('@/lib/hooks/use-middlewares', () => ({
  useMiddlewares: () => ({ data: [] }),
}))

vi.mock('@/lib/hooks/use-mcp-servers', () => ({
  useAllMcpTools: () => mockUseAllMcpTools(),
}))

vi.mock('@/lib/hooks/use-memory', () => ({
  useAgentMemorySettings: () => ({
    data: {
      memory_policy_override: 'inherit',
      memory_scopes_override: 'inherit',
      trigger_memory_policy_override: 'inherit',
    },
    isLoading: false,
  }),
  useUpdateAgentMemorySettings: () => ({
    mutateAsync: mockUpdateAgentMemorySettings,
    isPending: false,
  }),
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
  ModelSelect: ({
    value,
    onValueChange,
  }: {
    value: string
    onValueChange: (v: string) => void
  }) => (
    <select
      data-testid="model-select"
      value={value}
      onChange={(e) => onValueChange(e.target.value)}
    >
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
  runtime_name: 'test_agent',
  identity_mode: 'per_user',
  mcp_tools: [],
  skills: [],
  sub_agents: [],
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
    mockUpdateAgent.mockClear()
    mockRefetchAgent.mockClear()
    mockUseAgent.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      refetch: mockRefetchAgent,
    })
    mockUseModels.mockReturnValue({ data: [] })
    mockUseTools.mockReturnValue({ data: undefined })
    mockUseAllMcpTools.mockReturnValue({ data: [], isLoading: false })
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

  it('keeps the editor hidden behind one generic error surface and retries the agent query', async () => {
    mockUseAgent.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: mockRefetchAgent,
    })
    const user = userEvent.setup()

    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('문제가 발생했습니다')
    expect(screen.getByRole('alert')).toHaveTextContent(
      '에이전트를 찾을 수 없거나 접근 권한이 없습니다.',
    )
    expect(screen.queryByRole('tab', { name: '폼' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '다시 시도' }))
    expect(mockRefetchAgent).toHaveBeenCalledOnce()
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

  it('settings panel exposes memory override settings', () => {
    render(
      <SettingsPanel
        agentId="agent-1"
        imageUrl={null}
        name="Test Agent"
        identityMode="per_user"
        onIdentityModeChange={vi.fn()}
        runtimePolicy={null}
        onRuntimePolicyChange={vi.fn()}
        modelContextWindow={null}
      />,
    )

    expect(screen.getByText('메모리 정책')).toBeInTheDocument()
    expect(screen.getAllByText('전역 정책 상속')).toHaveLength(2)
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
    await user.click(within(screen.getByRole('banner')).getByRole('button', { name: '저장' }))
    expect(mockUpdateAgent).toHaveBeenCalledWith(expect.objectContaining({ name: 'Updated Agent' }))
  })

  it('calls updateAgent when only MCP tool selection changes', async () => {
    mockUseAgent.mockReturnValue({
      data: {
        ...fullAgent,
        tools: [],
        mcp_tools: [],
      },
      isLoading: false,
    })
    mockUseTools.mockReturnValue({ data: [] })
    mockUseAllMcpTools.mockReturnValue({
      data: [
        {
          id: 'mcp-tool-1',
          name: 'Repo Search',
          description: null,
          enabled: true,
          server_id: 'mcp-server-1',
          server_name: 'GitHub MCP',
        },
      ],
      isLoading: false,
    })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()

    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )

    const saveButton = screen.getByRole('button', { name: '저장' })
    await waitFor(() => expect(saveButton).toBeDisabled())

    const toolsSkillsBox = screen.getByText('도구·스킬').closest('.rounded-lg')
    expect(toolsSkillsBox).not.toBeNull()
    await user.click(within(toolsSkillsBox as HTMLElement).getByRole('button', { name: '추가' }))
    await user.click(screen.getByRole('tab', { name: /MCP/ }))

    const mcpRow = screen.getByText('Repo Search').closest('.rounded-lg')
    expect(mcpRow).not.toBeNull()
    await user.click(within(mcpRow as HTMLElement).getByRole('button', { name: /Repo Search/ }))

    await user.click(saveButton)

    expect(mockUpdateAgent).toHaveBeenCalledWith(
      expect.objectContaining({ mcp_tool_ids: ['mcp-tool-1'] }),
    )
  })

  it('uses the draft-owned runtime policy with the selected model context window', async () => {
    mockUseAgent.mockReturnValue({ data: fullAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: [{ id: 'model-1', context_window: 128000 }] })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()

    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )

    await user.click(screen.getByRole('tab', { name: '설정' }))

    expect(screen.getByText('권장 설정')).toBeInTheDocument()
    expect(
      screen.getByText(
        '이미 실행된 대화에는 영향을 주지 않습니다. 아직 실행하지 않은 대화와 새 대화에는 변경된 설정이 적용됩니다.',
      ),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '직접 설정' }))
    await user.click(screen.getByRole('switch', { name: '할 일 목록 사용' }))
    await user.click(screen.getByRole('button', { name: '검토만' }))
    const balancedButton = screen.getByRole('button', { name: '균형' })
    expect(balancedButton).toBeEnabled()
    await user.click(balancedButton)
    await user.click(within(screen.getByRole('banner')).getByRole('button', { name: '저장' }))

    expect(mockUpdateAgent).toHaveBeenCalledWith(
      expect.objectContaining({
        runtime_policy: {
          version: 1,
          filesystem: { mode: 'inspect' },
          todo: { enabled: false },
          summarization: { mode: 'preset', preset: 'balanced_context_v1' },
        },
      }),
    )
  })

  it('restores the recommended policy through the same settings-page draft', async () => {
    const storedPolicy = {
      version: 1 as const,
      filesystem: { mode: 'artifact_write' as const },
      todo: { enabled: false },
      summarization: { mode: 'auto' as const },
    }
    mockUseAgent.mockReturnValue({
      data: { ...fullAgent, runtime_policy: storedPolicy },
      isLoading: false,
    })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()

    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )

    await user.click(screen.getByRole('tab', { name: '설정' }))
    expect(screen.getByText('직접 설정', { selector: '[data-slot="badge"]' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '권장 설정 사용' }))
    await user.click(within(screen.getByRole('banner')).getByRole('button', { name: '저장' }))

    expect(mockUpdateAgent).toHaveBeenCalledWith(expect.objectContaining({ runtime_policy: null }))
  })

  it('uses the current agent model context window when the catalog is unavailable', async () => {
    mockUseAgent.mockReturnValue({
      data: {
        ...fullAgent,
        model: { id: 'model-1', display_name: 'GPT-4o', context_window: 64_000 },
      },
      isLoading: false,
    })
    mockUseModels.mockReturnValue({ data: undefined })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()

    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )

    await user.click(screen.getByRole('tab', { name: '설정' }))
    await user.click(screen.getByRole('button', { name: '직접 설정' }))
    const balancedButton = screen.getByRole('button', { name: '균형' })
    expect(balancedButton).toBeEnabled()
    await user.click(balancedButton)
    await user.click(within(screen.getByRole('banner')).getByRole('button', { name: '저장' }))

    expect(mockUpdateAgent).toHaveBeenCalledWith(
      expect.objectContaining({
        runtime_policy: expect.objectContaining({
          summarization: { mode: 'preset', preset: 'balanced_context_v1' },
        }),
      }),
    )
  })

  it.each([null, 0])(
    'blocks a stale balanced policy without a positive model context window until it is changed',
    async (contextWindow) => {
      const staleBalancedPolicy = {
        version: 1 as const,
        filesystem: { mode: 'artifact_write' as const },
        todo: { enabled: true },
        summarization: { mode: 'preset' as const, preset: 'balanced_context_v1' as const },
      }
      mockUseAgent.mockReturnValue({
        data: { ...fullAgent, runtime_policy: staleBalancedPolicy },
        isLoading: false,
      })
      mockUseModels.mockReturnValue({ data: [{ id: 'model-1', context_window: contextWindow }] })
      mockUseTools.mockReturnValue({ data: mockToolList })
      mockUseTriggers.mockReturnValue({ data: [] })
      const user = userEvent.setup()

      render(
        <AgentSettingsPage
          params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
        />,
      )

      await user.click(screen.getByRole('tab', { name: '설정' }))
      const saveButton = within(screen.getByRole('banner')).getByRole('button', { name: '저장' })
      expect(saveButton).toBeDisabled()
      await user.click(saveButton)
      expect(mockUpdateAgent).not.toHaveBeenCalled()

      await user.click(screen.getByRole('button', { name: '자동' }))
      expect(saveButton).toBeEnabled()
      await user.click(saveButton)
      expect(mockUpdateAgent).toHaveBeenCalledWith(
        expect.objectContaining({
          runtime_policy: expect.objectContaining({ summarization: { mode: 'auto' } }),
        }),
      )
    },
  )

  it('fails closed for a stale balanced policy when the selected model is unknown', async () => {
    const staleBalancedPolicy = {
      version: 1 as const,
      filesystem: { mode: 'artifact_write' as const },
      todo: { enabled: true },
      summarization: { mode: 'preset' as const, preset: 'balanced_context_v1' as const },
    }
    mockUseAgent.mockReturnValue({
      data: {
        ...fullAgent,
        model: null,
        runtime_policy: staleBalancedPolicy,
      },
      isLoading: false,
    })
    mockUseModels.mockReturnValue({ data: undefined })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const user = userEvent.setup()

    render(
      <AgentSettingsPage
        params={{ agentId: 'agent-1' } as unknown as Promise<{ agentId: string }>}
      />,
    )

    await user.click(screen.getByRole('tab', { name: '설정' }))
    const saveButton = within(screen.getByRole('banner')).getByRole('button', { name: '저장' })
    expect(saveButton).toBeDisabled()
    await user.click(saveButton)
    expect(mockUpdateAgent).not.toHaveBeenCalled()
  })

  // FormMode 내부 동작(모델 셀렉트, 도구 체크박스 토글, 트리거 CRUD 등)은
  // form-mode 컴포넌트 단위 테스트와 e2e/smoke로 분리된다 (페이지 단위에서 제외).
})
