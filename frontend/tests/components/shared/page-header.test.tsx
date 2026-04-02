import { render, screen } from "../../test-utils"
import { PageHeader } from "@/components/shared/page-header"

describe("PageHeader", () => {
  it("renders title", () => {
    render(<PageHeader title="Agent List" />)
    expect(screen.getByRole("heading", { name: "Agent List" })).toBeInTheDocument()
  })

  it("renders description when provided", () => {
    render(<PageHeader title="Agent List" description="Manage your agents" />)
    expect(screen.getByText("Manage your agents")).toBeInTheDocument()
  })

  it("renders action when provided", () => {
    render(
      <PageHeader
        title="Agent List"
        action={<button>Create Agent</button>}
      />
    )
    expect(screen.getByRole("button", { name: "Create Agent" })).toBeInTheDocument()
  })

  it("does not render description when not provided", () => {
    render(<PageHeader title="Agent List" />)
    expect(screen.queryByText("Manage your agents")).not.toBeInTheDocument()
  })
})
