import { render, screen } from "../test-utils"
import AgentSettingsPage from "@/app/agents/[agentId]/settings/page"
import { mockAgent, mockModelList, mockToolList, mockTriggerList } from "../mocks/fixtures"

vi.mock("next/link", () => ({
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

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

// Mock React.use() for params Promise
vi.mock("react", async () => {
  const actual = await vi.importActual("react")
  return {
    ...actual,
    use: (value: unknown) => {
      if (value && typeof value === "object" && "agentId" in value) return value
      return (actual as Record<string, unknown>).use(value)
    },
  }
})

const mockUseAgent = vi.fn()
const mockUseModels = vi.fn()
const mockUseTools = vi.fn()
const mockUseTriggers = vi.fn()

const mockUpdateAgent = vi.fn().mockResolvedValue({})
const mockDeleteAgent = vi.fn().mockResolvedValue({})

vi.mock("@/lib/hooks/use-agents", () => ({
  useAgent: (...args: unknown[]) => mockUseAgent(...args),
  useUpdateAgent: () => ({ mutateAsync: mockUpdateAgent, isPending: false }),
  useDeleteAgent: () => ({ mutateAsync: mockDeleteAgent, isPending: false }),
}))

vi.mock("@/lib/hooks/use-models", () => ({
  useModels: () => mockUseModels(),
}))

vi.mock("@/lib/hooks/use-tools", () => ({
  useTools: () => mockUseTools(),
}))

const mockCreateTrigger = vi.fn().mockResolvedValue({})
const mockUpdateTrigger = vi.fn()
const mockDeleteTrigger = vi.fn()

vi.mock("@/lib/hooks/use-triggers", () => ({
  useTriggers: (...args: unknown[]) => mockUseTriggers(...args),
  useCreateTrigger: () => ({ mutateAsync: mockCreateTrigger, isPending: false }),
  useUpdateTrigger: () => ({ mutate: mockUpdateTrigger }),
  useDeleteTrigger: () => ({ mutate: mockDeleteTrigger }),
}))

describe("AgentSettingsPage", () => {
  beforeEach(() => {
    mockUseAgent.mockReturnValue({ data: undefined, isLoading: false })
    mockUseModels.mockReturnValue({ data: undefined })
    mockUseTools.mockReturnValue({ data: undefined })
    mockUseTriggers.mockReturnValue({ data: undefined })
  })

  it("shows loading skeleton when agent is loading", () => {
    mockUseAgent.mockReturnValue({ data: undefined, isLoading: true })
    const { container } = render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it("renders agent name input when agent loaded", () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    expect(screen.getByText("이름")).toBeInTheDocument()
    expect(screen.getByDisplayValue("Test Agent")).toBeInTheDocument()
  })

  it("renders system prompt textarea", () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    expect(screen.getByText("시스템 프롬프트")).toBeInTheDocument()
    expect(
      screen.getByDisplayValue("You are a helpful assistant.")
    ).toBeInTheDocument()
  })

  it("renders model selector", () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    expect(screen.getByText("모델")).toBeInTheDocument()
  })

  it("renders tool checkboxes", () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    expect(screen.getByText("연결된 도구")).toBeInTheDocument()
    const checkboxes = screen.getAllByRole("checkbox")
    expect(checkboxes.length).toBe(mockToolList.length)
  })

  it("shows save and delete buttons", () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    expect(screen.getByText("저장")).toBeInTheDocument()
    expect(screen.getByText("에이전트 삭제")).toBeInTheDocument()
  })

  it("shows trigger section", () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: mockTriggerList })
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    expect(screen.getByText("자동 실행 (트리거)")).toBeInTheDocument()
    expect(screen.getByText("활성")).toBeInTheDocument()
  })

  it("shows empty tool message when no tools exist", () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: [] })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    expect(screen.getByText(/등록된 도구가 없습니다/)).toBeInTheDocument()
  })

  it("renders description input with agent description", () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    expect(screen.getByText("설명")).toBeInTheDocument()
    expect(screen.getByDisplayValue("A test agent")).toBeInTheDocument()
  })

  it("shows back to chat link", () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    expect(screen.getByText("채팅으로 돌아가기")).toBeInTheDocument()
  })

  it("renders add trigger button", () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    expect(screen.getByText("자동 실행 추가")).toBeInTheDocument()
  })

  it("submits trigger form when filled and clicked", async () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    // Open form
    await user.click(screen.getByText("자동 실행 추가"))
    // Fill interval
    const intervalInput = screen.getByPlaceholderText("10")
    await user.clear(intervalInput)
    await user.type(intervalInput, "30")
    // Fill message
    const messageInput = screen.getByPlaceholderText("예: 한글과컴퓨터 최신 뉴스를 검색하고 요약해줘")
    await user.type(messageInput, "테스트 메시지")
    // Submit
    await user.click(screen.getByText("트리거 추가"))
    expect(mockCreateTrigger).toHaveBeenCalledWith(
      expect.objectContaining({
        trigger_type: "interval",
        input_message: "테스트 메시지",
      })
    )
  })

  it("cancels trigger form", async () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    await user.click(screen.getByText("자동 실행 추가"))
    expect(screen.getByText("실행 간격 (분)")).toBeInTheDocument()
    await user.click(screen.getByText("취소"))
    // Form should disappear, add button should reappear
    expect(screen.queryByText("실행 간격 (분)")).not.toBeInTheDocument()
    expect(screen.getByText("자동 실행 추가")).toBeInTheDocument()
  })

  it("shows trigger form when add trigger is clicked", async () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    await user.click(screen.getByText("자동 실행 추가"))
    expect(screen.getByText("실행 간격 (분)")).toBeInTheDocument()
    expect(screen.getByText("실행 시 보낼 메시지")).toBeInTheDocument()
    expect(screen.getByText("트리거 추가")).toBeInTheDocument()
    expect(screen.getByText("취소")).toBeInTheDocument()
  })

  it("renders page title with agent name", () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    expect(screen.getByText("에이전트 설정: Test Agent")).toBeInTheDocument()
  })

  it("calls updateAgent when save button clicked", async () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    await user.click(screen.getByText("저장"))
    expect(mockUpdateAgent).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Test Agent",
        system_prompt: "You are a helpful assistant.",
      })
    )
  })

  it("toggles tool checkbox", async () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    const checkboxes = screen.getAllByRole("checkbox")
    // First checkbox should be checked (agent has tool-1)
    expect(checkboxes[0]).toBeChecked()
    // Toggle it off
    await user.click(checkboxes[0])
    expect(checkboxes[0]).not.toBeChecked()
    // Toggle it back on
    await user.click(checkboxes[0])
    expect(checkboxes[0]).toBeChecked()
  })

  it("shows trigger with pause/resume button", () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: mockTriggerList })
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    // Active trigger should have a pause button
    expect(screen.getByLabelText("일시정지")).toBeInTheDocument()
    // Paused trigger should have a resume button
    expect(screen.getByLabelText("재개")).toBeInTheDocument()
    // Delete button
    expect(screen.getAllByLabelText("트리거 삭제").length).toBe(2)
  })

  it("calls updateTrigger when pause button clicked", async () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: mockTriggerList })
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    await user.click(screen.getByLabelText("일시정지"))
    expect(mockUpdateTrigger).toHaveBeenCalledWith({
      triggerId: "trigger-1",
      data: { status: "paused" },
    })
  })

  it("calls deleteTrigger when delete button clicked", async () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: mockModelList })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: mockTriggerList })
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()
    render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    const deleteButtons = screen.getAllByLabelText("트리거 삭제")
    await user.click(deleteButtons[0])
    expect(mockDeleteTrigger).toHaveBeenCalledWith("trigger-1")
  })

  it("shows model skeleton when models not loaded", () => {
    mockUseAgent.mockReturnValue({ data: mockAgent, isLoading: false })
    mockUseModels.mockReturnValue({ data: undefined })
    mockUseTools.mockReturnValue({ data: mockToolList })
    mockUseTriggers.mockReturnValue({ data: [] })
    const { container } = render(
      <AgentSettingsPage
        params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>}
      />
    )
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })
})
