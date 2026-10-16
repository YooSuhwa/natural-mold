import { render, screen } from '../../../../tests/test-utils'
import { describe, expect, it } from 'vitest'

import { PersonalAccountBadge } from '../personal-account-badge'

describe('PersonalAccountBadge', () => {
  it('marks personal account types and explains that sharing is off', () => {
    render(<PersonalAccountBadge personal />)
    const label = screen.getByText('개인 계정')
    expect(label.parentElement).toHaveAttribute('title', expect.stringContaining('공유'))
    // Screen readers get the reason too, not only the hover title.
    expect(screen.getByText(/공유하거나 소유권을 이전할 수 없습니다/)).toHaveClass('sr-only')
  })

  it('renders nothing for other credential types', () => {
    const { container } = render(<PersonalAccountBadge personal={false} />)
    expect(container).toBeEmptyDOMElement()
  })
})
