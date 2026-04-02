import { render, screen } from "../../test-utils"
import { MessageBubble } from "@/components/chat/message-bubble"
import type { Message } from "@/lib/types"

const userMessage: Message = {
  id: "msg-1",
  conversation_id: "conv-1",
  role: "user",
  content: "Hello, how are you?",
  tool_calls: null,
  tool_call_id: null,
  created_at: "2026-01-01T00:00:00Z",
}

const assistantMessage: Message = {
  id: "msg-2",
  conversation_id: "conv-1",
  role: "assistant",
  content: "I'm doing great!",
  tool_calls: null,
  tool_call_id: null,
  created_at: "2026-01-01T00:00:01Z",
}

const toolMessage: Message = {
  id: "msg-3",
  conversation_id: "conv-1",
  role: "tool",
  content: "Tool result data here",
  tool_calls: null,
  tool_call_id: "tc-1",
  created_at: "2026-01-01T00:00:02Z",
}

const assistantWithToolCalls: Message = {
  id: "msg-4",
  conversation_id: "conv-1",
  role: "assistant",
  content: "Let me search that for you.",
  tool_calls: [{ name: "Web Search", args: { query: "test" } }],
  tool_call_id: null,
  created_at: "2026-01-01T00:00:03Z",
}

describe("MessageBubble", () => {
  it("renders user message content", () => {
    render(<MessageBubble message={userMessage} />)
    expect(screen.getByText("Hello, how are you?")).toBeInTheDocument()
  })

  it("renders assistant message content", () => {
    render(<MessageBubble message={assistantMessage} />)
    expect(screen.getByText("I'm doing great!")).toBeInTheDocument()
  })

  it("renders tool role message with result prefix", () => {
    render(<MessageBubble message={toolMessage} />)
    expect(screen.getByText(/도구 결과:/)).toBeInTheDocument()
    expect(screen.getByText("Tool result data here")).toBeInTheDocument()
  })

  it("renders tool calls when present", () => {
    render(<MessageBubble message={assistantWithToolCalls} />)
    expect(screen.getByText("Web Search")).toBeInTheDocument()
    expect(screen.getByText("완료")).toBeInTheDocument()
  })

  it("does not render tool calls section when absent", () => {
    render(<MessageBubble message={assistantMessage} />)
    expect(screen.queryByText("완료")).not.toBeInTheDocument()
  })

  it("renders user message with user icon", () => {
    const { container } = render(<MessageBubble message={userMessage} />)
    // User messages are right-justified
    const wrapper = container.firstChild as HTMLElement
    expect(wrapper.className).toContain("justify-end")
  })

  it("renders assistant message without justify-end", () => {
    const { container } = render(<MessageBubble message={assistantMessage} />)
    const wrapper = container.firstChild as HTMLElement
    expect(wrapper.className).not.toContain("justify-end")
  })

  it("does not render empty content", () => {
    const emptyAssistant: Message = {
      ...assistantMessage,
      content: "",
      tool_calls: [{ name: "Search", args: {} }],
    }
    render(<MessageBubble message={emptyAssistant} />)
    expect(screen.getByText("Search")).toBeInTheDocument()
    // No message bubble for empty content
    expect(screen.queryByText("I'm doing great!")).not.toBeInTheDocument()
  })
})
