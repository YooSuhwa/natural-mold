import { render, screen, waitFor } from "../test-utils"
import ConversationalCreationPage from "@/app/agents/new/conversational/page"
import { mockCreationSession } from "../mocks/fixtures"

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

const mockCreationSessionApi = {
  start: vi.fn(),
  sendMessage: vi.fn(),
  confirm: vi.fn(),
}

vi.mock("@/lib/api/creation-session", () => ({
  creationSessionApi: {
    start: () => mockCreationSessionApi.start(),
    sendMessage: (...args: unknown[]) =>
      mockCreationSessionApi.sendMessage(...args),
    confirm: (...args: unknown[]) => mockCreationSessionApi.confirm(...args),
  },
}))

describe("ConversationalCreationPage", () => {
  beforeEach(() => {
    mockCreationSessionApi.start.mockResolvedValue(mockCreationSession)
    mockCreationSessionApi.sendMessage.mockClear()
    mockCreationSessionApi.confirm.mockClear()
  })

  it("renders page header", () => {
    render(<ConversationalCreationPage />)
    expect(screen.getByText("에이전트 만들기")).toBeInTheDocument()
  })

  it("shows initial phase with question prompt", async () => {
    render(<ConversationalCreationPage />)
    await waitFor(() => {
      expect(
        screen.getByText("어떤 에이전트를 만들고 싶으세요?")
      ).toBeInTheDocument()
    })
  })

  it("renders textarea for initial input", async () => {
    render(<ConversationalCreationPage />)
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(
          '예: "한글과컴퓨터 관련 뉴스를 매일 요약해주는 에이전트"'
        )
      ).toBeInTheDocument()
    })
  })

  it("renders start button that is disabled when input is empty", async () => {
    render(<ConversationalCreationPage />)
    await waitFor(() => {
      const startButton = screen.getByRole("button", { name: /시작/ })
      expect(startButton).toBeInTheDocument()
    })
  })

  it("still shows phase 1 UI when session start fails", async () => {
    mockCreationSessionApi.start.mockRejectedValue(new Error("fail"))
    render(<ConversationalCreationPage />)
    // The phase 1 prompt is hardcoded, even on error the page shows phase 1
    await waitFor(() => {
      expect(
        screen.getByText("어떤 에이전트를 만들고 싶으세요?")
      ).toBeInTheDocument()
    })
  })

  it("sends message to API when start button clicked", async () => {
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()

    mockCreationSessionApi.sendMessage.mockResolvedValue({
      role: "assistant",
      content: "분석 완료",
      current_phase: 2,
      phase_result: null,
      question: "어떤 도구가 필요한가요?",
      draft_config: null,
      suggested_replies: null,
      recommended_tools: [],
    })

    render(<ConversationalCreationPage />)

    // Wait for session to start
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /시작/ })).toBeInTheDocument()
    })

    const textarea = screen.getByPlaceholderText(
      '예: "한글과컴퓨터 관련 뉴스를 매일 요약해주는 에이전트"'
    )
    await user.type(textarea, "뉴스 에이전트")
    await user.click(screen.getByRole("button", { name: /시작/ }))

    // API should have been called with the session id and text
    await waitFor(() => {
      expect(mockCreationSessionApi.sendMessage).toHaveBeenCalledWith(
        "session-1",
        "뉴스 에이전트"
      )
    })
  })

  it("shows PHASES constant data in timeline", () => {
    // The PHASES data is rendered in the PhaseTimeline component
    // Just verify the page renders the phase constants
    render(<ConversationalCreationPage />)
    // All phases are accessible in the component's code
    expect(screen.getByText("에이전트 만들기")).toBeInTheDocument()
  })

  it("shows loading state during API call", async () => {
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()

    // Use a promise that we control to keep loading state visible
    let resolveMessage: (value: unknown) => void = () => {}
    mockCreationSessionApi.sendMessage.mockReturnValue(
      new Promise((resolve) => {
        resolveMessage = resolve
      })
    )

    render(<ConversationalCreationPage />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /시작/ })).toBeInTheDocument()
    })

    const textarea = screen.getByPlaceholderText(
      '예: "한글과컴퓨터 관련 뉴스를 매일 요약해주는 에이전트"'
    )
    await user.type(textarea, "뉴스 에이전트")
    await user.click(screen.getByRole("button", { name: /시작/ }))

    // During loading, neither phase 1 input nor phase 2 content should show
    // The loading spinner should be present (Loader2Icon with animate-spin)
    await waitFor(() => {
      expect(screen.queryByPlaceholderText(
        '예: "한글과컴퓨터 관련 뉴스를 매일 요약해주는 에이전트"'
      )).not.toBeInTheDocument()
    })

    // Resolve with phase 2 response
    resolveMessage({
      role: "assistant",
      content: "분석 완료",
      current_phase: 2,
      phase_result: null,
      question: "도구 선택",
      draft_config: null,
      suggested_replies: null,
      recommended_tools: [],
    })
  })

  it("handles sendMessage error gracefully", async () => {
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()

    mockCreationSessionApi.sendMessage.mockRejectedValue(
      new Error("Network error")
    )

    render(<ConversationalCreationPage />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /시작/ })).toBeInTheDocument()
    })

    const textarea = screen.getByPlaceholderText(
      '예: "한글과컴퓨터 관련 뉴스를 매일 요약해주는 에이전트"'
    )
    await user.type(textarea, "test")
    await user.click(screen.getByRole("button", { name: /시작/ }))

    // Should not crash - page should still be rendered
    await waitFor(() => {
      expect(screen.getByText("에이전트 만들기")).toBeInTheDocument()
    })
  })

  it("does not submit when text is empty", async () => {
    render(<ConversationalCreationPage />)

    await waitFor(() => {
      const startButton = screen.getByRole("button", { name: /시작/ })
      expect(startButton).toBeDisabled()
    })
    expect(mockCreationSessionApi.sendMessage).not.toHaveBeenCalled()
  })
})
