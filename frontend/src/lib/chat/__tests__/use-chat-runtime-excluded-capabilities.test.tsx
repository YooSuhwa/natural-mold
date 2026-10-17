/**
 * F-06 / plan 13.6 — the legacy runtime reads the `moldy.excluded_capabilities` SSE
 * event live and from an active-run attach replay (reconnect) into the banner state.
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider as JotaiProvider, createStore } from 'jotai'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { streamResumeAttach } from '@/lib/sse/stream-resume-attach'
import type { ConversationRun, Message, SSEEvent } from '@/lib/types'
import {
  chatExcludedCapabilitiesAtom,
  dismissExcludedCapabilitiesAtom,
} from '@/lib/stores/chat-excluded-capabilities'
import { useChatRuntime } from '../use-chat-runtime'

vi.mock('@/lib/sse/stream-resume-attach', () => ({ streamResumeAttach: vi.fn() }))
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))
vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))

const NO_MESSAGES: Message[] = []
const GROUPS = [{ type: 'mcp_tool', reason: 'denied', count: 1 }]

async function* cleanRun(): AsyncGenerator<SSEEvent> {
  yield { event: 'message_start', id: 'msg-2-1', data: { id: 'msg-2', role: 'assistant' } }
  yield { event: 'message_end', id: 'msg-2-2', data: { content: '', usage: {} } }
}

async function* events(): AsyncGenerator<SSEEvent> {
  yield { event: 'message_start', id: 'msg-1-1', data: { id: 'msg-1', role: 'assistant' } }
  yield {
    event: 'moldy.excluded_capabilities',
    id: 'msg-1-2',
    data: { items: GROUPS, total: 1 },
  }
  yield { event: 'message_end', id: 'msg-1-3', data: { content: '', usage: {} } }
}

function wrapper(store: ReturnType<typeof createStore>) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <JotaiProvider store={store}>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </JotaiProvider>
    )
  }
}

function activeRun(): ConversationRun {
  return {
    id: 'run-1',
    conversation_id: 'conv-1',
    agent_id: 'agent-1',
    status: 'running',
    source: 'chat',
    parent_run_id: null,
    worker_instance_id: null,
    interrupt_id: null,
    last_event_id: null,
    input_preview: null,
    error_code: null,
    error_message: null,
    cancel_requested_at: null,
    started_at: null,
    heartbeat_at: null,
    completed_at: null,
    created_at: '2026-10-17T00:00:00.000Z',
    updated_at: '2026-10-17T00:00:00.000Z',
    metrics: null,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useChatRuntime excluded_capabilities (F-06 / 13.6)', () => {
  it('stores the live run-start event for the conversation', async () => {
    const store = createStore()
    const { result } = renderHook(
      () => useChatRuntime({ messages: NO_MESSAGES, streamFn: events, conversationId: 'conv-1' }),
      { wrapper: wrapper(store) },
    )
    await act(async () => {
      await result.current.sendMessage('hi')
    })
    expect(store.get(chatExcludedCapabilitiesAtom)['conv-1']?.notice).toEqual({
      runKey: 'msg-1',
      groups: GROUPS,
      dismissed: false,
    })
  })

  it('restores it from an active-run attach replay and keeps a prior dismissal', async () => {
    const store = createStore()
    store.set(chatExcludedCapabilitiesAtom, {
      // Same run, stale groups: the replay must overwrite them but keep the dismissal.
      'conv-1': { runs: ['msg-1'], notice: { runKey: 'msg-1', groups: [], dismissed: false } },
    })
    store.set(dismissExcludedCapabilitiesAtom, 'conv-1')
    vi.mocked(streamResumeAttach).mockReturnValue(events())
    const run = activeRun()
    renderHook(
      () =>
        useChatRuntime({
          messages: NO_MESSAGES,
          streamFn: events,
          conversationId: 'conv-1',
          activeRun: run,
        }),
      { wrapper: wrapper(store) },
    )
    await waitFor(() => expect(streamResumeAttach).toHaveBeenCalled())
    await waitFor(() =>
      expect(store.get(chatExcludedCapabilitiesAtom)['conv-1']?.notice).toEqual({
        runKey: 'msg-1',
        groups: GROUPS,
        dismissed: true,
      }),
    )
  })

  it('a later clean run clears it and a replay of the older run does not resurrect it', async () => {
    const store = createStore()
    let streamFn: () => AsyncGenerator<SSEEvent> = events
    const { result } = renderHook(
      () =>
        useChatRuntime({
          messages: NO_MESSAGES,
          streamFn: () => streamFn(),
          conversationId: 'conv-1',
        }),
      { wrapper: wrapper(store) },
    )
    await act(async () => {
      await result.current.sendMessage('first')
    })
    expect(store.get(chatExcludedCapabilitiesAtom)['conv-1']?.notice?.groups).toEqual(GROUPS)

    streamFn = cleanRun
    await act(async () => {
      await result.current.sendMessage('second')
    })
    expect(store.get(chatExcludedCapabilitiesAtom)['conv-1']?.notice).toBeNull()

    streamFn = events // replay of run 1 (e.g. a late attach)
    await act(async () => {
      await result.current.sendMessage('replay')
    })
    expect(store.get(chatExcludedCapabilitiesAtom)['conv-1']?.notice).toBeNull()
  })
})
