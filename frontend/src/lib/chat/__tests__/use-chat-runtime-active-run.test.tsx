import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import { StreamApiError, StreamHttpError } from '@/lib/sse/parse-sse'
import { streamResumeAttach } from '@/lib/sse/stream-resume-attach'
import type { ConversationRun, Message, SSEEvent } from '@/lib/types'
import { useChatRuntime } from '../use-chat-runtime'

const jotaiMocks = vi.hoisted(() => ({
  setAtom: vi.fn(),
  translate: (key: string) => key,
}))

vi.mock('next-intl', () => ({
  useTranslations: () => jotaiMocks.translate,
}))

vi.mock('jotai', async () => {
  const actual = await vi.importActual<typeof import('jotai')>('jotai')
  return {
    ...actual,
    useSetAtom: () => jotaiMocks.setAtom,
    useAtomValue: () => undefined,
  }
})

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))

vi.mock('@/lib/api/conversation-runs', () => ({
  conversationRunsApi: { cancel: vi.fn() },
}))

vi.mock('@/lib/sse/stream-resume-attach', () => ({
  streamResumeAttach: vi.fn(),
}))

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

function activeRun(status: ConversationRun['status'] = 'running'): ConversationRun {
  return {
    id: 'run-1',
    conversation_id: 'conversation-1',
    agent_id: 'agent-1',
    status,
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
    created_at: '2026-06-11T00:00:00.000Z',
    updated_at: '2026-06-11T00:00:00.000Z',
  }
}

async function* emptyStream(): AsyncGenerator<SSEEvent> {}

async function* completedAttachStream(): AsyncGenerator<SSEEvent> {
  yield {
    event: 'message_start',
    id: 'run-1-1',
    data: { id: 'run-1', role: 'assistant' },
  }
  yield {
    event: 'content_delta',
    id: 'run-1-2',
    data: { content: 'reattached answer' },
  }
  yield {
    event: 'message_end',
    id: 'run-1-3',
    data: { content: '', usage: {} },
  }
}

async function* staleAttachStream(): AsyncGenerator<SSEEvent> {
  yield {
    event: 'stale',
    id: 'run-1-stale',
    data: { reason: 'run_worker_lost', last_event_id: null },
  }
}

async function* failedAttachStream(): AsyncGenerator<SSEEvent> {
  throw new StreamHttpError(404, 'Not Found')
}

async function* resumeNotFoundAttachStream(): AsyncGenerator<SSEEvent> {
  throw new StreamApiError(404, 'RESUME_NOT_FOUND', 'missing')
}

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

function threadTexts(runtime: ReturnType<typeof useChatRuntime>['runtime']): string[] {
  return runtime.thread
    .getState()
    .messages.map((message) =>
      message.content.map((part) => (part.type === 'text' ? part.text : '')).join(''),
    )
}

describe('useChatRuntime active run attach', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('attaches to an active run on mount and commits replayed assistant content', async () => {
    vi.mocked(streamResumeAttach).mockReturnValue(completedAttachStream())
    const commitSpy = vi.fn()

    renderHook(
      () =>
        useChatRuntime({
          messages: [],
          streamFn: emptyStream,
          conversationId: 'conversation-1',
          activeRun: activeRun(),
          onMessagesCommit: commitSpy,
        }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => {
      expect(streamResumeAttach).toHaveBeenCalledWith(
        'conversation-1',
        'run-1',
        undefined,
        expect.any(AbortSignal),
        expect.any(Function),
      )
    })
    await waitFor(() => {
      const committed = commitSpy.mock.calls[0]?.[0] as Message[] | undefined
      expect(committed?.some((message) => message.content === 'reattached answer')).toBe(true)
    })
  })

  it('detaches active run streams on unmount without sending a server cancel', async () => {
    const started = deferred()
    const release = deferred()
    const captured: { signal: AbortSignal | null } = { signal: null }
    vi.mocked(streamResumeAttach).mockImplementation(
      async function* (_conversationId, _runId, _lastEventId, signal) {
        captured.signal = signal ?? null
        started.resolve()
        await release.promise
      },
    )

    const { unmount } = renderHook(
      () =>
        useChatRuntime({
          messages: [],
          streamFn: emptyStream,
          conversationId: 'conversation-1',
          activeRun: activeRun(),
        }),
      { wrapper: createWrapper() },
    )

    await started.promise
    act(() => {
      unmount()
    })
    release.resolve()

    expect(captured.signal?.aborted).toBe(true)
    expect(conversationRunsApi.cancel).not.toHaveBeenCalled()
  })

  it('shows a stale warning message when active run attach receives a stale lifecycle event', async () => {
    vi.mocked(streamResumeAttach).mockReturnValue(staleAttachStream())

    const { result } = renderHook(
      () =>
        useChatRuntime({
          messages: [],
          streamFn: emptyStream,
          conversationId: 'conversation-1',
          activeRun: activeRun(),
        }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => {
      expect(toast.warning).toHaveBeenCalledWith('stale', { id: 'chat-stream-stale' })
    })
    await waitFor(() => {
      expect(threadTexts(result.current.runtime)).toContain('stale')
    })
  })

  it('keeps the same active run attach alive when callback dependencies change', async () => {
    const captured: { signal: AbortSignal | null } = { signal: null }
    vi.mocked(streamResumeAttach).mockImplementation(
      async function* (_conversationId, _runId, _lastEventId, signal) {
        captured.signal = signal ?? null
        await new Promise<void>((resolve) => {
          if (signal?.aborted) {
            resolve()
            return
          }
          signal?.addEventListener('abort', () => resolve(), { once: true })
        })
      },
    )

    const { rerender } = renderHook(
      (props: { onStreamEnd: () => void }) =>
        useChatRuntime({
          messages: [],
          streamFn: emptyStream,
          conversationId: 'conversation-1',
          activeRun: activeRun(),
          onStreamEnd: props.onStreamEnd,
        }),
      { wrapper: createWrapper(), initialProps: { onStreamEnd: vi.fn() } },
    )

    await waitFor(() => {
      expect(streamResumeAttach).toHaveBeenCalledTimes(1)
    })

    rerender({ onStreamEnd: vi.fn() })

    expect(streamResumeAttach).toHaveBeenCalledTimes(1)
    expect(captured.signal?.aborted).toBe(false)
  })

  it('진행 중인 로컬 stream이 있으면 active run attach가 stream을 빼앗지 않는다', async () => {
    const started = deferred()
    const release = deferred()
    let localStreamAborted = false
    const blockingStreamFn = (_content: string, signal: AbortSignal) =>
      (async function* (): AsyncGenerator<SSEEvent> {
        started.resolve()
        await release.promise
        localStreamAborted = signal.aborted
      })()

    const { result, rerender } = renderHook(
      (props: { activeRun: ConversationRun | null }) =>
        useChatRuntime({
          messages: [],
          streamFn: blockingStreamFn,
          conversationId: 'conversation-1',
          activeRun: props.activeRun,
        }),
      { wrapper: createWrapper(), initialProps: { activeRun: null as ConversationRun | null } },
    )

    let sendPromise: Promise<void> = Promise.resolve()
    act(() => {
      sendPromise = result.current.sendMessage('hello')
    })
    await started.promise

    // envelope refetch 가 방금 시작된 run 을 active 로 보고하는 상황 재현
    rerender({ activeRun: activeRun() })
    await act(async () => {
      await Promise.resolve()
    })

    expect(streamResumeAttach).not.toHaveBeenCalled()
    release.resolve()
    await act(async () => {
      await sendPromise
    })
    expect(localStreamAborted).toBe(false)
  })

  it('정상 완료한 run의 stale envelope에는 재attach하지 않는다', async () => {
    const completedStreamFn = (
      _content: string,
      _signal: AbortSignal,
      options?: { onRunId?: (id: string) => void },
    ) =>
      (async function* (): AsyncGenerator<SSEEvent> {
        options?.onRunId?.('run-1')
        yield { event: 'message_start', id: 'run-1-1', data: { id: 'run-1', role: 'assistant' } }
        yield { event: 'message_end', id: 'run-1-2', data: { content: 'done', usage: {} } }
      })()

    const { result, rerender } = renderHook(
      (props: { activeRun: ConversationRun | null }) =>
        useChatRuntime({
          messages: [],
          streamFn: completedStreamFn,
          conversationId: 'conversation-1',
          activeRun: props.activeRun,
        }),
      { wrapper: createWrapper(), initialProps: { activeRun: null as ConversationRun | null } },
    )

    await act(async () => {
      await result.current.sendMessage('hello')
    })

    // 완료 후 envelope 이 아직 run-1 을 active 로 보고하는 stale 스냅샷 상황
    rerender({ activeRun: activeRun() })
    await act(async () => {
      await Promise.resolve()
    })

    expect(streamResumeAttach).not.toHaveBeenCalled()
  })

  it('네트워크 실패로 중단된 run은 같은 마운트에서 attach로 복구된다', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    const failingStreamFn = (
      _content: string,
      _signal: AbortSignal,
      options?: { onRunId?: (id: string) => void },
    ) =>
      (async function* (): AsyncGenerator<SSEEvent> {
        options?.onRunId?.('run-1')
        yield { event: 'content_delta', id: 'run-1-1', data: { delta: 'partial' } }
        // 4xx 는 withAutoResume 가 재시도하지 않고 즉시 throw — 테스트 결정성 확보
        throw new StreamHttpError(404, 'connection dropped')
      })()

    const { result, rerender } = renderHook(
      (props: { activeRun: ConversationRun | null }) =>
        useChatRuntime({
          messages: [],
          streamFn: failingStreamFn,
          conversationId: 'conversation-1',
          activeRun: props.activeRun,
        }),
      { wrapper: createWrapper(), initialProps: { activeRun: null as ConversationRun | null } },
    )

    await act(async () => {
      await result.current.sendMessage('hello')
    })
    expect(streamResumeAttach).not.toHaveBeenCalled()

    // 서버에는 run-1 이 여전히 active — envelope 이 이를 보고하면 attach 로 복구
    vi.mocked(streamResumeAttach).mockReturnValue(completedAttachStream())
    rerender({ activeRun: activeRun() })

    await waitFor(() => {
      expect(streamResumeAttach).toHaveBeenCalledWith(
        'conversation-1',
        'run-1',
        undefined,
        expect.any(AbortSignal),
        expect.any(Function),
      )
    })
    consoleError.mockRestore()
  })

  it('attach 도중 unmount 되면 이후 onStreamEnd/commit 콜백이 실행되지 않는다', async () => {
    const started = deferred()
    const release = deferred()
    vi.mocked(streamResumeAttach).mockImplementation(async function* () {
      started.resolve()
      await release.promise
    })
    const onStreamEnd = vi.fn()
    const onMessagesCommit = vi.fn()

    const { unmount } = renderHook(
      () =>
        useChatRuntime({
          messages: [],
          streamFn: emptyStream,
          conversationId: 'conversation-1',
          activeRun: activeRun(),
          onStreamEnd,
          onMessagesCommit,
        }),
      { wrapper: createWrapper() },
    )

    await started.promise
    act(() => {
      unmount()
    })
    release.resolve()
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(onStreamEnd).not.toHaveBeenCalled()
    expect(onMessagesCommit).not.toHaveBeenCalled()
  })

  it('handles active run attach failures without an unhandled rejection', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(streamResumeAttach).mockReturnValue(failedAttachStream())
    const messages: Message[] = []
    const run = activeRun()

    renderHook(
      () =>
        useChatRuntime({
          messages,
          streamFn: emptyStream,
          conversationId: 'conversation-1',
          activeRun: run,
        }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => {
      expect(consoleError).toHaveBeenCalledWith(
        '[useChatRuntime] Active run attach failed:',
        expect.any(Error),
      )
    })
    expect(streamResumeAttach).toHaveBeenCalledTimes(1)

    consoleError.mockRestore()
  })

  it('shows stale notice when active run attach finds no replay trace', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(streamResumeAttach).mockReturnValue(resumeNotFoundAttachStream())

    const { result } = renderHook(
      () =>
        useChatRuntime({
          messages: [],
          streamFn: emptyStream,
          conversationId: 'conversation-1',
          activeRun: activeRun(),
        }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => {
      expect(toast.warning).toHaveBeenCalledWith('stale', { id: 'chat-stream-stale' })
    })
    expect(threadTexts(result.current.runtime)).toContain('stale')
    expect(consoleError).not.toHaveBeenCalled()

    consoleError.mockRestore()
  })
})
