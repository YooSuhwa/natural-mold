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
const connect = vi.hoisted(() => ({
  agentIds: [] as string[],
  onConnected: null as (() => void) | null,
}))
vi.mock('@/components/readiness/auth-card-connect', () => ({
  AuthCardConnect: ({ agentId, onConnected }: { agentId: string; onConnected: () => void }) => {
    connect.agentIds.push(agentId)
    connect.onConnected = onConnected
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
  permission: Partial<HiTLContextValue> = {},
) {
  const value: HiTLContextValue = {
    onResumeDecisions: vi.fn(),
    registerDecision: registerDecision as unknown as HiTLContextValue['registerDecision'],
    agentId,
  }
  const queryClient = new QueryClient()
  const tree = (currentArgs: Record<string, unknown>, gate: Partial<HiTLContextValue>) => (
    <QueryClientProvider client={queryClient}>
      <HiTLContext.Provider value={{ ...value, ...gate }}>
        <Card cardArgs={currentArgs} />
      </HiTLContext.Provider>
    </QueryClientProvider>
  )
  const rendered = render(tree(cardArgs, permission))
  return {
    ...rendered,
    update: (nextArgs: Record<string, unknown>, gate: Partial<HiTLContextValue>) =>
      rendered.rerender(tree(nextArgs, gate)),
  }
}

describe('AuthRequiredCard OAuth connect (G-05)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    connect.agentIds = []
    connect.onConnected = null
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

  it.each([{ canRespond: false }, { canRespond: true, canRespondTo: () => false }])(
    'blocks OAuth, credential binding and decisions until the exact slot is ready',
    (gate) => {
      const open = vi.spyOn(window, 'open').mockReturnValue(null)
      try {
        const register = vi.fn()
        const mixed = {
          ...args,
          items: [...args.items, { resource_name: 'Notion', fixable_by: 'me' }],
        }
        renderWith(register, mixed, 'agent-main', gate)
        for (const name of ['connectOAuth', 'connect', 'retry', 'skip', 'cancel']) {
          const button = screen.getByRole('button', { name })
          expect(button).toBeDisabled()
          fireEvent.click(button)
        }
        expect(connect.agentIds).toEqual([])
        expect(open).not.toHaveBeenCalled()
        expect(credentialsApi.startOAuth2).not.toHaveBeenCalled()
        expect(register).not.toHaveBeenCalled()
      } finally {
        open.mockRestore()
      }
    },
  )

  it('ignores an already open OAuth popup and a late binding when the checkpoint rotates', async () => {
    const popup = { location: { href: 'about:blank' }, close: vi.fn(), focus: vi.fn() }
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window)
    try {
      vi.mocked(credentialsApi.startOAuth2).mockResolvedValue({
        authorization_url: 'https://auth.example/authorize',
        state: 's',
      })
      const register = vi.fn()
      const mixed = {
        ...args,
        items: [...args.items, { resource_name: 'Notion', fixable_by: 'me' }],
      }
      const { update } = renderWith(register, mixed, 'agent-main', {
        canRespond: true,
        approvalGeneration: 'before',
      })
      const previousBinding = connect.onConnected
      expect(previousBinding).toBeTypeOf('function')
      fireEvent.click(screen.getByRole('button', { name: 'connectOAuth' }))
      await screen.findByText('oauthWaiting')
      update(mixed, { canRespond: false, approvalGeneration: null })
      update(mixed, { canRespond: true, approvalGeneration: 'after' })
      expect(screen.getByRole('button', { name: 'connectOAuth' })).toBeEnabled()
      act(() => {
        previousBinding?.()
        window.dispatchEvent(
          new MessageEvent('message', {
            data: { type: 'moldy.oauth.completed', credentialId: 'cred-1' },
            origin: window.location.origin,
          }),
        )
      })
      expect(register).not.toHaveBeenCalled()
    } finally {
      open.mockRestore()
    }
  })

  it('rechecks a displayed slot before opening OAuth or sending a decision', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    try {
      let admitted = true
      const register = vi.fn()
      renderWith(register, args, undefined, { canRespond: true, canRespondTo: () => admitted })
      const button = screen.getByRole('button', { name: 'connectOAuth' })
      expect(button).toBeEnabled()
      admitted = false
      fireEvent.click(button)
      fireEvent.click(screen.getByRole('button', { name: 'retry' }))
      expect(open).not.toHaveBeenCalled()
      expect(credentialsApi.startOAuth2).not.toHaveBeenCalled()
      expect(register).not.toHaveBeenCalled()
    } finally {
      open.mockRestore()
    }
  })

  it('does not navigate a popup when authorization starts after the request was replaced', async () => {
    const popup = { location: { href: 'about:blank' }, close: vi.fn(), focus: vi.fn() }
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window)
    try {
      const authorization =
        Promise.withResolvers<Awaited<ReturnType<typeof credentialsApi.startOAuth2>>>()
      vi.mocked(credentialsApi.startOAuth2).mockReturnValue(authorization.promise)
      const register = vi.fn()
      const { update } = renderWith(register, args, undefined, {
        canRespond: true,
        approvalGeneration: 'first',
      })
      fireEvent.click(screen.getByRole('button', { name: 'connectOAuth' }))
      await waitFor(() => expect(credentialsApi.startOAuth2).toHaveBeenCalledOnce())
      update(args, { canRespond: true, approvalGeneration: 'next' })
      await act(async () =>
        authorization.resolve({
          state: 'old',
          authorization_url: 'https://auth.example/authorize',
        }),
      )
      expect(popup.location.href).toBe('about:blank')
      expect(popup.focus).not.toHaveBeenCalled()
      expect(screen.queryByText('oauthWaiting')).toBeNull()
      expect(register).not.toHaveBeenCalled()
    } finally {
      open.mockRestore()
    }
  })

  it('keeps an accepted answer visible until another checkpoint uses this slot', async () => {
    const { update } = renderWith(vi.fn().mockResolvedValue(undefined), args, undefined, {
      canRespond: true,
      approvalGeneration: 'first',
    })
    fireEvent.click(screen.getByRole('button', { name: 'skip' }))
    await screen.findByText('sent')
    update(args, { canRespond: false, approvalGeneration: null })
    expect(screen.getByText('sent')).toBeInTheDocument()
    update(args, { canRespond: true, approvalGeneration: 'next' })
    expect(screen.getByRole('button', { name: 'skip' })).toBeEnabled()
  })
})
