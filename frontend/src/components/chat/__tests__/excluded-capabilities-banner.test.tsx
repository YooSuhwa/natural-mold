import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Provider as JotaiProvider, createStore } from 'jotai'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { ExcludedCapabilitiesBanner } from '../excluded-capabilities-banner'
import { ChatConversationContext } from '../conversation-context'
import {
  chatExcludedCapabilitiesAtom,
  setExcludedCapabilitiesAtom,
  startExcludedCapabilitiesRunAtom,
  type ExcludedCapabilityGroup,
} from '@/lib/stores/chat-excluded-capabilities'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}(${Object.values(params).join('/')})` : key,
}))

const DENIED: ExcludedCapabilityGroup = { type: 'tool', reason: 'denied', count: 2 }
const BROKEN: ExcludedCapabilityGroup = { type: 'skill', reason: 'broken', count: 1 }

function renderBanner(store = createStore(), conversationId: string | null = 'conv-1') {
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <JotaiProvider store={store}>
        <ChatConversationContext.Provider value={conversationId}>
          {children}
        </ChatConversationContext.Provider>
      </JotaiProvider>
    )
  }
  return render(<ExcludedCapabilitiesBanner />, { wrapper: Wrapper })
}

function seed(groups: ExcludedCapabilityGroup[], conversationId: string | null = 'conv-1') {
  const store = createStore()
  store.set(setExcludedCapabilitiesAtom, { conversationId, runId: 'run-1', groups })
  return store
}

describe('ExcludedCapabilitiesBanner', () => {
  it('renders nothing without exclusions or for another conversation', () => {
    const { container } = renderBanner()
    expect(container.querySelector('[data-moldy-excluded-capabilities]')).toBeNull()
    const other = renderBanner(seed([DENIED], 'conv-OTHER'))
    expect(other.container.querySelector('[data-moldy-excluded-capabilities]')).toBeNull()
  })

  it('shows per-type counts with reasons and the request-access hint for denied', () => {
    renderBanner(seed([BROKEN, DENIED]))
    expect(screen.getByRole('status')).toHaveTextContent('title')
    expect(screen.getByText('group(type.skill/1/reason.broken)')).toBeInTheDocument()
    expect(screen.getByText('group(type.tool/2/reason.denied)')).toBeInTheDocument()
    expect(screen.getByText('requestAccessHint')).toBeInTheDocument()
  })

  it('omits the request-access hint when only broken links were excluded', () => {
    renderBanner(seed([BROKEN]))
    expect(screen.queryByText('requestAccessHint')).toBeNull()
  })

  it('renders nothing (no crash) for a partial notice without groups', () => {
    const store = createStore()
    store.set(chatExcludedCapabilitiesAtom, {
      'conv-1': { runs: ['run-1'], notice: { runKey: 'run-1' } as never },
    })
    const { container } = renderBanner(store)
    expect(container.querySelector('[data-moldy-excluded-capabilities]')).toBeNull()
  })

  it('maps unknown types to the generic label', () => {
    renderBanner(seed([{ type: 'future_kind', reason: 'denied', count: 1 }]))
    expect(screen.getByText('group(type.other/1/reason.denied)')).toBeInTheDocument()
  })

  it('works for legacy panels without a conversation id', () => {
    renderBanner(seed([DENIED], null), null)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('dismisses; a replay of the same run stays dismissed, a new run shows it again', async () => {
    const user = userEvent.setup()
    const store = seed([DENIED])
    renderBanner(store)
    await user.click(screen.getByRole('button', { name: 'dismiss' }))
    expect(screen.queryByRole('status')).toBeNull()
    expect(store.get(chatExcludedCapabilitiesAtom)['conv-1']?.notice?.dismissed).toBe(true)

    act(() => {
      store.set(setExcludedCapabilitiesAtom, {
        conversationId: 'conv-1',
        runId: 'run-1',
        groups: [DENIED],
      })
    })
    expect(screen.queryByRole('status')).toBeNull()

    act(() => {
      store.set(setExcludedCapabilitiesAtom, {
        conversationId: 'conv-1',
        runId: 'run-2',
        groups: [BROKEN],
      })
    })
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('a later clean run hides it; a replay of the older run does not bring it back', () => {
    const store = seed([DENIED])
    renderBanner(store)
    expect(screen.getByRole('status')).toBeInTheDocument()

    act(() =>
      store.set(startExcludedCapabilitiesRunAtom, { conversationId: 'conv-1', runId: 'run-2' }),
    )
    expect(screen.queryByRole('status')).toBeNull()

    act(() => {
      store.set(startExcludedCapabilitiesRunAtom, { conversationId: 'conv-1', runId: 'run-1' })
      store.set(setExcludedCapabilitiesAtom, {
        conversationId: 'conv-1',
        runId: 'run-1',
        groups: [DENIED],
      })
    })
    expect(screen.queryByRole('status')).toBeNull()
  })
})
