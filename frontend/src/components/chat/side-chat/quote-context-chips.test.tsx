import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { ResourceContextReference } from '@/lib/chat/context/resource-context'
import { QuoteContextChips } from './quote-context-chips'

vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))

function Harness() {
  const [refs, setRefs] = useState<readonly ResourceContextReference[]>([
    {
      kind: 'conversation',
      id: '11111111-1111-4111-8111-111111111111',
      message_id: 'm1',
      message_role: 'assistant',
      quote: '원문 일부',
      label: '출처 대화',
    },
  ])
  return (
    <QuoteContextChips
      references={refs}
      onRemove={() => setRefs([])}
      onUpdate={(_, next) => setRefs([next])}
    />
  )
}

describe('quote chip preview and editor', () => {
  it('shows source, role and the edited comment on hover after closing the editor', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const chip = screen.getByRole('button', { name: 'quoteNumber' })
    await user.hover(chip)
    await waitFor(() => expect(screen.getByRole('tooltip')).toHaveTextContent('assistantMessage'))
    await user.click(chip)
    await user.type(screen.getByRole('textbox', { name: 'comment' }), '내 의견')
    await user.click(screen.getByRole('button', { name: 'saveComment' }))
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'saveComment' })).not.toBeInTheDocument(),
    )
    await user.hover(chip)
    await waitFor(() => expect(screen.getByRole('tooltip')).toHaveTextContent('내 의견'))
    expect(screen.getByRole('tooltip')).toHaveTextContent('출처 대화')
  })
})
