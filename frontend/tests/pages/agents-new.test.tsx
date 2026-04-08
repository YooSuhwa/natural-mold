import { render, screen } from '../test-utils'
import AgentNewPage from '@/app/agents/new/page'

vi.mock('next/link', () => ({
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

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

describe('AgentNewPage', () => {
  it('renders hero section with title', () => {
    render(<AgentNewPage />)
    expect(screen.getByText('생성하려는 에이전트에 대해 알려주세요')).toBeInTheDocument()
  })

  it('renders chat input textarea', () => {
    render(<AgentNewPage />)
    const textarea = screen.getByRole('textbox')
    expect(textarea).toBeInTheDocument()
  })

  it('manual option links to correct path', () => {
    render(<AgentNewPage />)
    const manualLink = screen.getByText('에이전트 직접 만들기').closest('a')
    expect(manualLink).toHaveAttribute('href', '/agents/new/manual')
  })

  it('template option links to correct path', () => {
    render(<AgentNewPage />)
    const templateLink = screen.getByText('템플릿으로 만들기').closest('a')
    expect(templateLink).toHaveAttribute('href', '/agents/new/template')
  })
})
