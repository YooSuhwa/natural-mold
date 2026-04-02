import { render, screen } from "../../test-utils"
import { ToolCallDisplay } from "@/components/chat/tool-call-display"

describe("ToolCallDisplay", () => {
  it("shows tool name", () => {
    render(
      <ToolCallDisplay
        toolCall={{ name: "Web Search", args: { query: "test" } }}
        status="completed"
      />
    )
    expect(screen.getByText("Web Search")).toBeInTheDocument()
  })

  it('shows "호출 중..." for calling status', () => {
    render(
      <ToolCallDisplay
        toolCall={{ name: "Web Search", args: {} }}
        status="calling"
      />
    )
    expect(screen.getByText("호출 중...")).toBeInTheDocument()
  })

  it('shows "완료" for completed status', () => {
    render(
      <ToolCallDisplay
        toolCall={{ name: "Web Search", args: {} }}
        status="completed"
      />
    )
    expect(screen.getByText("완료")).toBeInTheDocument()
  })

  it("shows result text when provided", () => {
    render(
      <ToolCallDisplay
        toolCall={{ name: "Web Search", args: {} }}
        status="completed"
        result="Found 10 results"
      />
    )
    expect(screen.getByText("Found 10 results")).toBeInTheDocument()
  })

  it("does not show result section when not provided", () => {
    render(
      <ToolCallDisplay
        toolCall={{ name: "Web Search", args: {} }}
        status="completed"
      />
    )
    expect(screen.queryByText("Found 10 results")).not.toBeInTheDocument()
  })
})
