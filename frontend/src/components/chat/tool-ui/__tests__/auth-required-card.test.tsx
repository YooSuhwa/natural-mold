import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { HiTLContext } from '@/lib/chat/hitl-context'
import { AuthRequiredCard } from '../auth-required-card'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

const route = vi.hoisted(() => ({ params: {} as Record<string, string> }))
vi.mock('next/navigation', () => ({ useParams: () => route.params }))
vi.mock('@/components/readiness/auth-card-connect', () => ({
  AuthCardConnect: ({ onConnected }: { onConnected: () => void }) => (
    <button type="button" onClick={onConnected}>
      connected-in-panel
    </button>
  ),
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

function Card({ attempt }: { attempt?: number }) {
  const cardArgs = attempt === undefined ? args : { ...args, attempt }
  return renderCard({ args: cardArgs, status: { type: 'requires-action' } })
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

  it('sends the attempt it answers so a stale card is refused by the server', async () => {
    const registerDecision = vi.fn().mockResolvedValue(undefined)
    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <Card attempt={2} />
      </HiTLContext.Provider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'skip' }))
    await waitFor(() => expect(registerDecision).toHaveBeenCalled())
    expect(registerDecision.mock.calls[0]?.[1]).toEqual({
      type: 'respond',
      message: 'skip',
      attempt: 2,
    })
  })
})

describe('AuthRequiredCard connect (G-04, S22)', () => {
  it('opens the agent check from the chat route and answers retry once connected', async () => {
    route.params = { agentId: 'agent-1' }
    const registerDecision = vi.fn().mockResolvedValue(undefined)
    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <Card />
      </HiTLContext.Provider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'connected-in-panel' }))
    await waitFor(() =>
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        { type: 'respond', message: 'retry' },
        'retry',
        'int-1',
      ),
    )
    route.params = {}
  })

  it('has no connect button outside an agent route and says a re-ask is still not connected', () => {
    route.params = {}
    render(<Card attempt={2} />)
    expect(screen.queryByRole('button', { name: 'connected-in-panel' })).not.toBeInTheDocument()
    expect(screen.getByText('stillNotConnected')).toBeInTheDocument()
  })
})
