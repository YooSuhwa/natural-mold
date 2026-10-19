/**
 * G-05 done-when (plan 13.6): the pending `auth_required` card comes back after a page
 * reload (thread state, latest `attempt`) and after an SSE reconnect (the backend
 * re-synthesizes the interrupt with its kind), without duplicating the card.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AIMessage, HumanMessage, type BaseMessage } from '@langchain/core/messages'
import { createStore, Provider } from 'jotai'
import type { ReactNode } from 'react'
import type { UseStreamReturn } from '@langchain/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMoldyLangGraphStream } from '../use-moldy-langgraph-stream'
import { useStreamInterruptView } from '../use-stream-interrupt-view'
import {
  resolvedInterruptToolCallsFromDecisions,
  standardPayloadFromInterrupt,
} from '../hitl-interrupts'

const mocks = vi.hoisted(() => {
  const STREAM_CONTROLLER = Symbol('STREAM_CONTROLLER')
  const snapshot = new Map() // stable: read through useSyncExternalStore
  const metadataStore = { subscribe: vi.fn(() => () => {}), getSnapshot: vi.fn(() => snapshot) }
  const thread = {
    interrupts: [],
    subscribe: vi.fn(async () => ({ unsubscribe: vi.fn() })),
    values: { async *[Symbol.asyncIterator]() {} },
  }
  const stream = {
    messages: [] as unknown[],
    values: { messages: [] },
    interrupts: [],
    isLoading: false,
    submit: vi.fn(),
    respond: vi.fn(),
    respondAll: vi.fn(),
    stop: vi.fn(),
    disconnect: vi.fn(),
    getThread: vi.fn(() => thread),
    [STREAM_CONTROLLER]: { messageMetadataStore: metadataStore },
  }
  return {
    STREAM_CONTROLLER,
    stream,
    apiFetch: vi.fn(),
    useExternalMessageConverter: vi.fn((options: { messages: readonly unknown[] }) => {
      void options
      return [] as unknown[]
    }),
  }
})

vi.mock('../moldy-agent-transport', () => ({
  createMoldyAgentTransport: vi.fn((conversationId: string) => ({
    conversationId,
    setStateHydrationListener: vi.fn(),
    setRunStartAcceptedListener: vi.fn(),
    readState: vi.fn(async () => null),
    submitQueuedInput: vi.fn(),
    retryFailedInput: vi.fn(),
  })),
}))
vi.mock('@langchain/react', () => ({
  STREAM_CONTROLLER: mocks.STREAM_CONTROLLER,
  useChannel: vi.fn(() => []),
  useChannelEffect: vi.fn(),
  useStream: vi.fn(() => mocks.stream),
}))
vi.mock('@assistant-ui/react', () => ({
  useExternalMessageConverter: mocks.useExternalMessageConverter,
  useExternalStoreRuntime: vi.fn((options: unknown) => ({ kind: 'runtime', options })),
}))
vi.mock('@assistant-ui/react-langchain', () => ({ convertLangChainBaseMessage: vi.fn() }))
vi.mock('@/lib/api/client', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))
vi.mock('sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), warning: vi.fn() } }))

const AUTH_VALUE = {
  type: 'auth_required',
  reason: 'expired',
  items: [
    {
      resource_name: 'Slack',
      tool_names: ['post_message'],
      credential_id: 'c1',
      fixable_by: 'me',
      connect: { kind: 'oauth' },
    },
  ],
  allowed_decisions: ['respond'],
}

type ProjectedMessage = { id?: string; tool_calls?: readonly { id?: string; name?: string }[] }

function authCalls(messages: readonly ProjectedMessage[] | undefined) {
  return (messages ?? [])
    .flatMap((message) => message.tool_calls ?? [])
    .filter((call) => call.name === 'moldy_auth_required')
}

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <Provider store={createStore()}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </Provider>
  )
}

describe('auth_required card restoration (G-05)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.stream.messages = [
      new HumanMessage({ id: 'h-1', content: 'Slack에 올려줘' }),
      new AIMessage({ id: 'a-1', content: '' }),
    ]
  })

  it('restores the pending card with its latest attempt from thread state after a reload', async () => {
    const conversationId = '22222222-2222-4222-8222-222222222222'
    mocks.apiFetch.mockImplementation(async (path: string) =>
      path.endsWith('/run-inputs')
        ? { queue_paused: false, items: [] }
        : {
            values: { messages: [{ type: 'human', id: 'h-1', content: 'Slack에 올려줘' }] },
            tasks: [
              {
                id: 'run-1',
                name: 'interrupted',
                interrupts: [{ id: 'intr-auth', ns: [], value: { ...AUTH_VALUE, attempt: 2 } }],
              },
            ],
          },
    )

    renderHook(() => useMoldyLangGraphStream({ agentId: 'agent-1', conversationId }), { wrapper })

    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledWith(
        `/api/conversations/${conversationId}/langgraph/threads/${conversationId}/state`,
      )
      const options = mocks.useExternalMessageConverter.mock.calls.at(-1)?.[0] as
        | { messages: readonly ProjectedMessage[] }
        | undefined
      expect(authCalls(options?.messages)).toEqual([
        expect.objectContaining({ id: 'intr-auth:0#2' }),
      ])
    })
  })
})

function viewStream(interrupts: unknown[]) {
  return { interrupts, getThread: () => undefined } as unknown as UseStreamReturn<object>
}

describe('auth_required card after an SSE reconnect (G-05)', () => {
  const messages: BaseMessage[] = []

  it('shows one card when the reconnect re-synthesizes the interrupt the state already has', () => {
    // Reconnect synthesis (pending_input_requested_events) keeps `type` + `interrupt_id`.
    const synthesized = {
      id: 'intr-auth',
      value: { interrupt_id: 'intr-auth', ...AUTH_VALUE, attempt: 2 },
    }
    const fromState = { id: 'intr-auth', value: { ...AUTH_VALUE, attempt: 2 } }
    const { result } = renderHook(() =>
      useStreamInterruptView({
        conversationId: 'c-1',
        stream: viewStream([synthesized]),
        serverInterrupts: [fromState],
        messages,
      }),
    )

    expect(
      authCalls(result.current.messagesWithInterrupts as unknown as ProjectedMessage[]),
    ).toEqual([expect.objectContaining({ id: 'intr-auth:0#2' })])
  })

  it('keeps the re-ask visible when a replay still carries the answered attempt', () => {
    const answered = standardPayloadFromInterrupt({
      id: 'intr-auth',
      value: { ...AUTH_VALUE, attempt: 1 },
    })
    if (!answered) throw new Error('auth payload not recognized')
    const { result } = renderHook(() => {
      const view = useStreamInterruptView({
        conversationId: 'c-1',
        // reload state already has the re-ask; the replayed stream still has attempt 1
        stream: viewStream([{ id: 'intr-auth', value: { ...AUTH_VALUE, attempt: 1 } }]),
        serverInterrupts: [{ id: 'intr-auth', value: { ...AUTH_VALUE, attempt: 2 } }],
        messages,
      })
      return view
    })
    expect(result.current.payloads).toHaveLength(1)
    const resolved = resolvedInterruptToolCallsFromDecisions(answered, [
      { type: 'respond', message: 'retry', attempt: 1 },
    ])
    expect(resolved[0]?.toolCall.id).toBe('intr-auth:0#1')
    expect(
      authCalls(result.current.messagesWithInterrupts as unknown as ProjectedMessage[]),
    ).toEqual([expect.objectContaining({ id: 'intr-auth:0#2' })])
  })
})
