import { render, screen, waitFor } from "../../test-utils"
import { ConversationList } from "@/components/chat/conversation-list"
import { mockConversationList } from "../../mocks/fixtures"

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: React.ReactNode; href: string; [key: string]: unknown }) => (
    <a href={href} {...props}>{children}</a>
  ),
}))

const mockPush = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn() }),
  useParams: () => ({ conversationId: "conv-1" }),
  usePathname: () => "/agents/agent-1/conversations/conv-1",
}))

describe("ConversationList", () => {
  it("renders heading", () => {
    render(<ConversationList agentId="agent-1" />)
    expect(screen.getByText("대화 목록")).toBeInTheDocument()
  })

  it("renders new conversation button", () => {
    render(<ConversationList agentId="agent-1" />)
    expect(screen.getByRole("button", { name: "새 대화" })).toBeInTheDocument()
  })

  it("renders conversation list from API", async () => {
    render(<ConversationList agentId="agent-1" />)

    await waitFor(() => {
      expect(screen.getByText("Test Conversation")).toBeInTheDocument()
    })
    expect(screen.getByText("Second Conversation")).toBeInTheDocument()
  })

  it("renders conversation links with correct hrefs", async () => {
    render(<ConversationList agentId="agent-1" />)

    await waitFor(() => {
      expect(screen.getByText("Test Conversation")).toBeInTheDocument()
    })

    const links = screen.getAllByRole("link")
    expect(links[0]).toHaveAttribute("href", "/agents/agent-1/conversations/conv-1")
    expect(links[1]).toHaveAttribute("href", "/agents/agent-1/conversations/conv-2")
  })

  it("shows loading skeletons initially", () => {
    const { container } = render(<ConversationList agentId="agent-1" />)
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it("creates new conversation when button clicked", async () => {
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()
    render(<ConversationList agentId="agent-1" />)

    await waitFor(() => {
      expect(screen.getByText("Test Conversation")).toBeInTheDocument()
    })

    const newButton = screen.getByRole("button", { name: "새 대화" })
    await user.click(newButton)

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith(
        "/agents/agent-1/conversations/conv-new"
      )
    })
  })
})
