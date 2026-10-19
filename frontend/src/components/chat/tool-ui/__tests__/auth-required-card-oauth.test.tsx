import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { credentialsApi } from '@/lib/api/credentials'
import { HiTLContext, type HiTLContextValue } from '@/lib/chat/hitl-context'
import { AuthRequiredCard } from '../auth-required-card'

vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))
vi.mock('next/navigation', () => ({ useParams: () => ({}) }))
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }))
vi.mock('@/lib/api/credentials', () => ({ credentialsApi: { startOAuth2: vi.fn() } }))
const connect = vi.hoisted(() => ({ agentIds: [] as string[] }))
vi.mock('@/components/readiness/auth-card-connect', () => ({
  AuthCardConnect: ({ agentId }: { agentId: string }) => {
    connect.agentIds.push(agentId)
    return <span>bind-connect</span>
  },
}))

type Render = (props: { args: Record<string, unknown>; status: { type: string } }) => ReactNode
const renderCard = AuthRequiredCard as unknown as Render

const args = {
  reason: 'expired',
  attempt: 2,
  items: [
    {
      resource_name: 'Slack',
      tool_names: ['post_message'],
      credential_id: 'cred-1',
      fixable_by: 'me',
      connect: { kind: 'oauth' },
    },
  ],
  hitl_action_index: 0,
  hitl_total_actions: 1,
  hitl_interrupt_id: 'int-1',
}

function Card({ cardArgs }: { cardArgs: Record<string, unknown> }) {
  return renderCard({ args: cardArgs, status: { type: 'requires-action' } })
}

function renderWith(
  registerDecision: ReturnType<typeof vi.fn>,
  cardArgs: Record<string, unknown> = args,
  agentId?: string,
) {
  const value: HiTLContextValue = {
    onResumeDecisions: vi.fn(),
    registerDecision: registerDecision as unknown as HiTLContextValue['registerDecision'],
    agentId,
  }
  const queryClient = new QueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <HiTLContext.Provider value={value}>
        <Card cardArgs={cardArgs} />
      </HiTLContext.Provider>
    </QueryClientProvider>,
  )
}

describe('AuthRequiredCard OAuth connect (G-05)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    connect.agentIds = []
  })

  it('opens the OAuth popup and answers retry with the attempt once the popup completes', async () => {
    const popup = { location: { href: 'about:blank' }, close: vi.fn(), focus: vi.fn() }
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window)
    vi.mocked(credentialsApi.startOAuth2).mockResolvedValue({
      authorization_url: 'https://auth.example/authorize',
      state: 's',
    })
    const registerDecision = vi.fn().mockResolvedValue(undefined)
    renderWith(registerDecision)

    fireEvent.click(screen.getByRole('button', { name: 'connectOAuth' }))
    expect(await screen.findByText('oauthWaiting')).toBeInTheDocument()
    expect(credentialsApi.startOAuth2).toHaveBeenCalledWith('cred-1')
    expect(registerDecision).not.toHaveBeenCalled()

    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: { type: 'moldy.oauth.completed', credentialId: 'cred-1' },
          origin: window.location.origin,
        }),
      )
    })

    await waitFor(() =>
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        { type: 'respond', message: 'retry', attempt: 2 },
        'retry',
        'int-1',
      ),
    )
    open.mockRestore()
  })

  it('ignores the completion of another credential', async () => {
    const popup = { location: { href: 'about:blank' }, close: vi.fn(), focus: vi.fn() }
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window)
    vi.mocked(credentialsApi.startOAuth2).mockResolvedValue({
      authorization_url: 'https://auth.example/authorize',
      state: 's',
    })
    const registerDecision = vi.fn().mockResolvedValue(undefined)
    renderWith(registerDecision)

    fireEvent.click(screen.getByRole('button', { name: 'connectOAuth' }))
    await screen.findByText('oauthWaiting')
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: { type: 'moldy.oauth.completed', credentialId: 'cred-other' },
          origin: window.location.origin,
        }),
      )
    })

    expect(registerDecision).not.toHaveBeenCalled()
    open.mockRestore()
  })

  it('keeps the binding connect for non-OAuth items, on the thread agent', () => {
    const bindingArgs = {
      ...args,
      items: [{ resource_name: 'Notion', tool_names: ['search'], fixable_by: 'me' }],
    }
    renderWith(vi.fn(), bindingArgs, 'agent-side')

    expect(screen.getByText('bind-connect')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'connectOAuth' })).not.toBeInTheDocument()
    expect(connect.agentIds).toContain('agent-side')
  })
})
