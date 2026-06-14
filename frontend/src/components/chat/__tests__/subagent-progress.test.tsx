import { describe, expect, it } from 'vitest'
import { render, screen } from '../../../../tests/test-utils'
import { SubagentProgress } from '../subagent-progress'

describe('SubagentProgress', () => {
  it('renders nothing when no subagents are discovered', () => {
    const { container } = render(
      <SubagentProgress summary={{ total: 0, running: 0, completed: 0, failed: 0 }} />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('shows completed, running, and failed counts', () => {
    render(<SubagentProgress summary={{ total: 4, running: 1, completed: 2, failed: 1 }} />)

    expect(screen.getByText('서브 에이전트 3/4 완료')).toBeInTheDocument()
    expect(screen.getByText('1개 진행 중')).toBeInTheDocument()
    expect(screen.getByText('1개 실패')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: '서브 에이전트 진행률' })).toHaveAttribute(
      'value',
      '3',
    )
  })
})
