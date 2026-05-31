import { render, screen, userEvent } from '../../test-utils'
import {
  DomainIcon,
  DomainIconPicker,
  EmptyStateIcon,
  getDomainIconIdForResource,
} from '@/components/shared/icon'

describe('DomainIcon', () => {
  it('maps marketplace resource types to stable Lucide icon ids', () => {
    expect(getDomainIconIdForResource('agent')).toBe('agent')
    expect(getDomainIconIdForResource('skill')).toBe('skill')
    expect(getDomainIconIdForResource('mcp')).toBe('mcp')
    expect(getDomainIconIdForResource('unknown')).toBe('box')
  })

  it('renders category aliases through the shared registry', () => {
    const { container } = render(<DomainIcon iconId="scheduler" />)
    expect(container.querySelector('svg.lucide-calendar-clock')).toBeInTheDocument()
  })

  it('renders a framed empty-state icon by icon id', () => {
    const { container } = render(<EmptyStateIcon iconId="credential" />)
    expect(container.querySelector('svg.lucide-key-round')).toBeInTheDocument()
  })
})

describe('DomainIconPicker', () => {
  it('lets users choose from the Lucide allowlist', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<DomainIconPicker value="skill" onChange={onChange} />)

    await user.click(screen.getByRole('radio', { name: 'Agent' }))

    expect(onChange).toHaveBeenCalledWith('agent')
  })
})
