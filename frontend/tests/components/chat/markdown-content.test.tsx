import { render, screen } from "../../test-utils"
import { MarkdownContent } from "@/components/chat/markdown-content"

describe("MarkdownContent", () => {
  it("renders paragraphs", () => {
    render(<MarkdownContent content="Hello world" />)
    expect(screen.getByText("Hello world")).toBeInTheDocument()
  })

  it("renders bold text", () => {
    render(<MarkdownContent content="This is **bold** text" />)
    const bold = screen.getByText("bold")
    expect(bold.tagName).toBe("STRONG")
  })

  it("renders italic text", () => {
    render(<MarkdownContent content="This is *italic* text" />)
    const italic = screen.getByText("italic")
    expect(italic.tagName).toBe("EM")
  })

  it("renders links with target=_blank", () => {
    render(<MarkdownContent content="Visit [Google](https://google.com)" />)
    const link = screen.getByText("Google")
    expect(link.tagName).toBe("A")
    expect(link).toHaveAttribute("target", "_blank")
    expect(link).toHaveAttribute("rel", "noopener noreferrer")
    expect(link).toHaveAttribute("href", "https://google.com")
  })

  it("renders unordered lists", () => {
    const content = `- Item 1
- Item 2
- Item 3`
    render(<MarkdownContent content={content} />)
    expect(screen.getByText("Item 1")).toBeInTheDocument()
    expect(screen.getByText("Item 2")).toBeInTheDocument()
    expect(screen.getByText("Item 3")).toBeInTheDocument()
  })

  it("renders inline code", () => {
    render(<MarkdownContent content="Use `console.log` to debug" />)
    const code = screen.getByText("console.log")
    expect(code.tagName).toBe("CODE")
  })

  it("renders code blocks", () => {
    render(<MarkdownContent content={'```js\nconst x = 1\n```'} />)
    expect(screen.getByText("const x = 1")).toBeInTheDocument()
  })

  it("accepts custom className", () => {
    const { container } = render(
      <MarkdownContent content="Hello" className="custom-class" />
    )
    expect(container.firstChild).toHaveClass("custom-class")
  })

  it("renders ordered lists", () => {
    const content = `1. First\n2. Second\n3. Third`
    render(<MarkdownContent content={content} />)
    expect(screen.getByText("First")).toBeInTheDocument()
    expect(screen.getByText("Second")).toBeInTheDocument()
  })

  it("renders blockquotes", () => {
    render(<MarkdownContent content="> This is a quote" />)
    const quote = screen.getByText("This is a quote")
    expect(quote.closest("blockquote")).toBeInTheDocument()
  })

  it("renders headings", () => {
    render(<MarkdownContent content="# Heading 1" />)
    expect(screen.getByText("Heading 1")).toBeInTheDocument()
  })

  it("renders horizontal rules", () => {
    const content = "above\n\n---\n\nbelow"
    const { container } = render(<MarkdownContent content={content} />)
    const hrs = container.querySelectorAll("hr")
    expect(hrs.length).toBeGreaterThanOrEqual(1)
  })

  it("renders h2 headings", () => {
    render(<MarkdownContent content="## Heading 2" />)
    expect(screen.getByText("Heading 2")).toBeInTheDocument()
  })

  it("renders h3 headings", () => {
    render(<MarkdownContent content="### Heading 3" />)
    expect(screen.getByText("Heading 3")).toBeInTheDocument()
  })
})
