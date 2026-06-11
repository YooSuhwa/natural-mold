import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Message, SSEEvent } from '@/lib/types'
import { useChatRuntime } from '../use-chat-runtime'
import { streamRegenerate } from '@/lib/sse/stream-regenerate'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

vi.mock('jotai', async () => {
  const actual = await vi.importActual<typeof import('jotai')>('jotai')
  return {
    ...actual,
    useSetAtom: () => vi.fn(),
    useAtomValue: () => undefined,
  }
})

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/lib/sse/stream-regenerate', () => ({
  streamRegenerate: vi.fn(),
}))

const mockedStreamRegenerate = vi.mocked(streamRegenerate)

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

function msg(id: string, role: Message['role'], content = ''): Message {
  return {
    id,
    conversation_id: 'conv-1',
    role,
    content,
    tool_calls: null,
    tool_call_id: null,
    created_at: '2026-06-03T00:00:00Z',
    feedback: null,
    attachments: null,
    usage: null,
    parent_id: null,
    branch_checkpoint_id: null,
    branch_index: null,
    branch_total: null,
  }
}

async function* emptyRegenerateStream(): AsyncGenerator<SSEEvent> {
  yield {
    event: 'message_end',
    id: 'evt-end',
    data: { usage: {} },
  } as SSEEvent
}

async function* unusedStreamFn(): AsyncGenerator<SSEEvent> {
  throw new Error('new-message stream should not be used by regenerate tests')
}

describe('useChatRuntime — regenerate target selection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedStreamRegenerate.mockImplementation(emptyRegenerateStream)
  })

  it('uses bare regenerate when the visible assistant message only has a client-side stream id', async () => {
    const { result } = renderHook(
      () =>
        useChatRuntime({
          messages: [msg('user-1', 'user', '안녕'), msg('stream-active', 'assistant', '안녕!')],
          streamFn: unusedStreamFn as unknown as (
            content: string,
            signal: AbortSignal,
          ) => AsyncGenerator<SSEEvent>,
          conversationId: 'conv-1',
        }),
      { wrapper: createWrapper() },
    )

    await act(async () => {
      result.current.runtime.thread.getMessageById('stream-active').reload()
    })

    await waitFor(() => expect(mockedStreamRegenerate).toHaveBeenCalledTimes(1))
    expect(mockedStreamRegenerate.mock.calls[0]?.[0]).toBe('conv-1')
    expect(mockedStreamRegenerate.mock.calls[0]?.[1]).toBeUndefined()
  })

  it('passes the backend assistant message id when one is available', async () => {
    const assistantId = '11111111-1111-1111-1111-111111111111'
    const { result } = renderHook(
      () =>
        useChatRuntime({
          messages: [msg('user-1', 'user', '안녕'), msg(assistantId, 'assistant', '안녕!')],
          streamFn: unusedStreamFn as unknown as (
            content: string,
            signal: AbortSignal,
          ) => AsyncGenerator<SSEEvent>,
          conversationId: 'conv-1',
        }),
      { wrapper: createWrapper() },
    )

    await act(async () => {
      result.current.runtime.thread.getMessageById(assistantId).reload()
    })

    await waitFor(() => expect(mockedStreamRegenerate).toHaveBeenCalledTimes(1))
    expect(mockedStreamRegenerate.mock.calls[0]?.[0]).toBe('conv-1')
    expect(mockedStreamRegenerate.mock.calls[0]?.[1]).toBe(assistantId)
  })
})
