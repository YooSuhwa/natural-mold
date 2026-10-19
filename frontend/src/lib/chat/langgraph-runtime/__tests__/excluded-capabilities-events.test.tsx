/**
 * F-06 / plan 13.6 — the v3 run-start `moldy.excluded_capabilities` event fills the
 * conversation's banner state and is restored by the replay after a reload.
 */
import { act, renderHook } from '@testing-library/react'
import { Provider as JotaiProvider, createStore } from 'jotai'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnyStream } from '@langchain/react'
import {
  chatExcludedCapabilitiesAtom,
  dismissExcludedCapabilitiesAtom,
} from '@/lib/stores/chat-excluded-capabilities'
import { useLangGraphExcludedCapabilitiesEffects } from '../excluded-capabilities-events'

const mocks = vi.hoisted(() => ({ useChannelEffect: vi.fn() }))
vi.mock('@langchain/react', () => ({ useChannelEffect: mocks.useChannelEffect }))

type ChannelEffectOptions = { replay?: boolean; onEvent: (event: unknown) => void }

const EVENT = {
  method: 'custom',
  event_id: 'run-1:excluded_capabilities',
  seq: 2,
  params: {
    data: {
      name: 'moldy.excluded_capabilities',
      payload: { items: [{ type: 'tool', reason: 'denied', count: 2 }], total: 2 },
    },
  },
}

function running(runId: string, namespace: string[] = []) {
  return {
    method: 'lifecycle',
    event_id: `${runId}:lifecycle:running`,
    params: { namespace, data: { event: 'running' } },
  }
}

function mount(store: ReturnType<typeof createStore>) {
  function Wrapper({ children }: { children: ReactNode }) {
    return <JotaiProvider store={store}>{children}</JotaiProvider>
  }
  renderHook(
    () =>
      useLangGraphExcludedCapabilitiesEffects({
        stream: {} as AnyStream,
        conversationId: 'conv-1',
      }),
    { wrapper: Wrapper },
  )
  const options = mocks.useChannelEffect.mock.calls.at(-1)?.[2] as ChannelEffectOptions
  return options
}

beforeEach(() => {
  mocks.useChannelEffect.mockReset()
})

describe('useLangGraphExcludedCapabilitiesEffects', () => {
  it('subscribes with replay and stores the groups under the run key', () => {
    const store = createStore()
    const options = mount(store)
    expect(options).toEqual(expect.objectContaining({ replay: true }))

    act(() => options.onEvent(EVENT))

    expect(store.get(chatExcludedCapabilitiesAtom)['conv-1']?.notice).toEqual({
      runKey: 'run-1',
      groups: [{ type: 'tool', reason: 'denied', count: 2 }],
      dismissed: false,
    })
  })

  it('restores the banner on reload from the replayed event', () => {
    // A reload starts with an empty store; the persisted events are replayed in order.
    const store = createStore()
    const options = mount(store)
    act(() => {
      options.onEvent(running('run-1'))
      options.onEvent(EVENT)
    })
    expect(store.get(chatExcludedCapabilitiesAtom)['conv-1']?.notice?.groups).toHaveLength(1)
  })

  it('run N excluded, run N+1 clean: cleared, and a replay of N does not resurrect it', () => {
    const store = createStore()
    const options = mount(store)
    act(() => {
      options.onEvent(running('run-1'))
      options.onEvent(EVENT)
    })
    expect(store.get(chatExcludedCapabilitiesAtom)['conv-1']?.notice).not.toBeNull()

    act(() => options.onEvent(running('run-2')))
    expect(store.get(chatExcludedCapabilitiesAtom)['conv-1']?.notice).toBeNull()

    // Reconnect/reload replay of the older run after the newer one.
    act(() => {
      options.onEvent(running('run-1'))
      options.onEvent(EVENT)
    })
    expect(store.get(chatExcludedCapabilitiesAtom)['conv-1']?.notice).toBeNull()
  })

  it('ignores subgraph lifecycle events', () => {
    const store = createStore()
    const options = mount(store)
    act(() => {
      options.onEvent(running('run-1'))
      options.onEvent(EVENT)
      options.onEvent(running('run-sub', ['task:1']))
    })
    expect(store.get(chatExcludedCapabilitiesAtom)['conv-1']?.notice).not.toBeNull()
  })

  it('keeps a dismissal across a reconnect replay of the same run', () => {
    const store = createStore()
    const options = mount(store)
    act(() => options.onEvent(EVENT))
    act(() => store.set(dismissExcludedCapabilitiesAtom, 'conv-1'))
    act(() => options.onEvent(EVENT))
    expect(store.get(chatExcludedCapabilitiesAtom)['conv-1']?.notice?.dismissed).toBe(true)
  })

  it('ignores other custom events', () => {
    const store = createStore()
    const options = mount(store)
    act(() =>
      options.onEvent({
        method: 'custom',
        params: { data: { name: 'moldy.subagent_names', payload: { names: {} } } },
      }),
    )
    expect(store.get(chatExcludedCapabilitiesAtom)).toEqual({})
  })
})
