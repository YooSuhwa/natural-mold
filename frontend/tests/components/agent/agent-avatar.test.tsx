import { render, screen } from '../../test-utils'
import { AgentAvatar } from '@/components/agent/agent-avatar'

describe('AgentAvatar', () => {
  it('requests the lightweight preview variant for backend agent images', () => {
    render(<AgentAvatar imageUrl="/api/agents/a-1/image?t=123" name="Guide" />)

    const image = screen.getByRole('img', { name: 'Guide' })
    expect(image).toHaveAttribute(
      'src',
      'http://localhost:8001/api/agents/a-1/image?t=123&variant=preview',
    )
  })

  it('does not rewrite public asset image URLs', () => {
    render(<AgentAvatar imageUrl="/moldy.png" name="Moldy" publicAsset />)

    const image = screen.getByRole('img', { name: 'Moldy' })
    expect(image.getAttribute('src')).toContain('/moldy.png')
    expect(image.getAttribute('src')).not.toContain('variant=preview')
    expect(image.getAttribute('src')).not.toContain('localhost:8001')
  })
})
