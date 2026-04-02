import { render } from '../../test-utils'
import { AgentCardSkeleton } from '@/components/agent/agent-card-skeleton'

describe('AgentCardSkeleton', () => {
  it('renders without errors', () => {
    const { container } = render(<AgentCardSkeleton />)
    expect(container.firstChild).toBeInTheDocument()
  })

  it('has skeleton elements', () => {
    const { container } = render(<AgentCardSkeleton />)
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })
})
