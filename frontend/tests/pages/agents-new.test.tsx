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

describe('AgentNewPage', () => {
  it('renders two creation method options', () => {
    render(<AgentNewPage />)
    expect(screen.getByText('대화로 만들기')).toBeInTheDocument()
    expect(screen.getByText('템플릿으로 만들기')).toBeInTheDocument()
  })

  it('conversational option links to correct path', () => {
    render(<AgentNewPage />)
    const link = screen.getByText('시작하기').closest('a')
    expect(link).toHaveAttribute('href', '/agents/new/conversational')
  })

  it('template option links to correct path', () => {
    render(<AgentNewPage />)
    const link = screen.getByText('둘러보기').closest('a')
    expect(link).toHaveAttribute('href', '/agents/new/template')
  })

  it('renders page header', () => {
    render(<AgentNewPage />)
    expect(screen.getByText('새 에이전트 만들기')).toBeInTheDocument()
  })
})
