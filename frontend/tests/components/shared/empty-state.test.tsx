import { render, screen } from "../../test-utils"
import { EmptyState } from "@/components/shared/empty-state"

describe("EmptyState", () => {
  it("renders title and description", () => {
    render(
      <EmptyState
        title="No items"
        description="Create your first item to get started"
      />
    )
    expect(screen.getByText("No items")).toBeInTheDocument()
    expect(screen.getByText("Create your first item to get started")).toBeInTheDocument()
  })

  it("renders icon when provided", () => {
    render(
      <EmptyState
        icon={<span data-testid="test-icon">Icon</span>}
        title="No items"
      />
    )
    expect(screen.getByTestId("test-icon")).toBeInTheDocument()
  })

  it("renders action button when provided", () => {
    render(
      <EmptyState
        title="No items"
        action={<button>Create Item</button>}
      />
    )
    expect(screen.getByRole("button", { name: "Create Item" })).toBeInTheDocument()
  })

  it("does not render action when not provided", () => {
    render(<EmptyState title="No items" />)
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it("does not render description when not provided", () => {
    render(<EmptyState title="No items" />)
    // Only the title text should be present
    const texts = screen.getAllByText(/./i)
    expect(texts).toHaveLength(1)
  })
})
