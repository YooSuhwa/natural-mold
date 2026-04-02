import { render, screen } from "../../test-utils"
import { AgentCard } from "@/components/agent/agent-card"
import type { Agent } from "@/lib/types"

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: React.ReactNode; href: string; [key: string]: unknown }) => (
    <a href={href} {...props}>{children}</a>
  ),
}))

const activeAgent: Agent = {
  id: "agent-1",
  name: "Test Agent",
  description: "A test agent description",
  system_prompt: "You are a helpful assistant.",
  model: { id: "model-1", display_name: "GPT-4o" },
  tools: [
    { id: "tool-1", name: "Web Search" },
    { id: "tool-2", name: "Calculator" },
  ],
  status: "active",
  template_id: null,
  created_at: "2026-01-15T00:00:00Z",
  updated_at: "2026-01-15T00:00:00Z",
}

const errorAgent: Agent = {
  ...activeAgent,
  id: "agent-2",
  name: "Error Agent",
  status: "error",
}

const inactiveAgent: Agent = {
  ...activeAgent,
  id: "agent-3",
  name: "Inactive Agent",
  status: "inactive",
  description: null,
  tools: [],
}

describe("AgentCard", () => {
  it("renders agent name", () => {
    render(<AgentCard agent={activeAgent} />)
    expect(screen.getByText("Test Agent")).toBeInTheDocument()
  })

  it("renders agent description", () => {
    render(<AgentCard agent={activeAgent} />)
    expect(screen.getByText("A test agent description")).toBeInTheDocument()
  })

  it("does not render description when null", () => {
    render(<AgentCard agent={inactiveAgent} />)
    expect(screen.queryByText("A test agent description")).not.toBeInTheDocument()
  })

  it("shows active status badge", () => {
    render(<AgentCard agent={activeAgent} />)
    expect(screen.getByText("활성")).toBeInTheDocument()
  })

  it("shows error status badge", () => {
    render(<AgentCard agent={errorAgent} />)
    expect(screen.getByText("오류")).toBeInTheDocument()
  })

  it("shows inactive status badge", () => {
    render(<AgentCard agent={inactiveAgent} />)
    expect(screen.getByText("비활성")).toBeInTheDocument()
  })

  it("shows model name", () => {
    render(<AgentCard agent={activeAgent} />)
    expect(screen.getByText("GPT-4o")).toBeInTheDocument()
  })

  it("shows tool names", () => {
    render(<AgentCard agent={activeAgent} />)
    expect(screen.getByText("Web Search, Calculator")).toBeInTheDocument()
  })

  it("does not show tool section when no tools", () => {
    render(<AgentCard agent={inactiveAgent} />)
    expect(screen.queryByText("Web Search")).not.toBeInTheDocument()
  })

  it("links to correct agent URL", () => {
    render(<AgentCard agent={activeAgent} />)
    const link = screen.getByRole("link")
    expect(link).toHaveAttribute("href", "/agents/agent-1")
  })
})
