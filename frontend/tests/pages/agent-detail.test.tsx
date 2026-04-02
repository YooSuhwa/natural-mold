import { render, screen, waitFor } from "../test-utils"
import AgentPage from "@/app/agents/[agentId]/page"
import { mockConversationList } from "../mocks/fixtures"

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

const mockReplace = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: mockReplace }),
}))

const mockConversationsApi = {
  list: vi.fn(),
  create: vi.fn(),
}

vi.mock("@/lib/api/conversations", () => ({
  conversationsApi: {
    list: (...args: unknown[]) => mockConversationsApi.list(...args),
    create: (...args: unknown[]) => mockConversationsApi.create(...args),
  },
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

describe("AgentPage (redirect)", () => {
  beforeEach(() => {
    mockReplace.mockClear()
    mockConversationsApi.list.mockClear()
    mockConversationsApi.create.mockClear()
  })

  it("redirects to latest conversation when conversations exist", async () => {
    mockConversationsApi.list.mockResolvedValue(mockConversationList)

    render(<AgentPage params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>} />)

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/agents/agent-1/conversations/conv-1"
      )
    })
  })

  it("creates a new conversation and redirects when none exist", async () => {
    mockConversationsApi.list.mockResolvedValue([])
    mockConversationsApi.create.mockResolvedValue({
      id: "conv-new",
      agent_id: "agent-1",
    })

    render(<AgentPage params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>} />)

    await waitFor(() => {
      expect(mockConversationsApi.create).toHaveBeenCalledWith("agent-1")
      expect(mockReplace).toHaveBeenCalledWith(
        "/agents/agent-1/conversations/conv-new"
      )
    })
  })

  it("shows error message when API fails", async () => {
    mockConversationsApi.list.mockRejectedValue(new Error("fail"))

    render(<AgentPage params={{ agentId: "agent-1" } as unknown as Promise<{ agentId: string }>} />)

    await waitFor(() => {
      expect(
        screen.getByText("대화를 불러오는 데 실패했습니다.")
      ).toBeInTheDocument()
    })
  })
})
