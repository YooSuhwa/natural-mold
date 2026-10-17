import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { HiTLContext } from '@/lib/chat/hitl-context'
import { AuthRequiredCard } from '../auth-required-card'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

type Render = (props: { args: Record<string, unknown>; status: { type: string } }) => ReactNode
const renderCard = AuthRequiredCard as unknown as Render

const args = {
  reason: 'auth_needed',
  items: [
    { resource_name: 'Slack', tool_names: ['post_message'], fixable_by: 'me' },
    { resource_name: 'Jira', tool_names: ['create_issue'], fixable_by: 'owner' },
  ],
  hitl_action_index: 0,
  hitl_total_actions: 1,
  hitl_interrupt_id: 'int-1',
}

function Card() {
  return renderCard({ args, status: { type: 'requires-action' } })
}

describe('AuthRequiredCard', () => {
  it('lists the needed connections and marks owner-fixable ones', () => {
    render(<Card />)
    expect(screen.getByText('title')).toBeInTheDocument()
    expect(screen.getByText('reasonAuthNeeded')).toBeInTheDocument()
    expect(screen.getByText('Slack')).toBeInTheDocument()
    expect(screen.getByText('Jira')).toBeInTheDocument()
    expect(screen.getAllByText('ownerNotified')).toHaveLength(1)
  })

  it.each(['retry', 'skip', 'cancel'])('answers %s as a respond decision', async (response) => {
    const registerDecision = vi.fn().mockResolvedValue(undefined)
    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <Card />
      </HiTLContext.Provider>,
    )
    fireEvent.click(screen.getByRole('button', { name: response }))
    await waitFor(() => expect(screen.getByText('sent')).toBeInTheDocument())
    expect(registerDecision).toHaveBeenCalledWith(
      0,
      { type: 'respond', message: response },
      response,
      'int-1',
    )
  })

  it('shows an error and keeps the buttons when the resume fails', async () => {
    const registerDecision = vi.fn().mockRejectedValue(new Error('down'))
    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <Card />
      </HiTLContext.Provider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'retry' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('resumeFailed')
    expect(screen.getByRole('button', { name: 'retry' })).toBeEnabled()
  })
})
