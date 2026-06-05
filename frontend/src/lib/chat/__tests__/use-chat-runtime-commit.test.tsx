/**
 * `onMessagesCommit` 경로(빌더 / AssistantPanel / TestChatPanel) 회귀 가드.
 *
 * 회귀: stream 종료 시 finally 가 `onMessagesCommit(finalMsgs)` 로 streaming
 * 메시지를 부모 state 에 옮기는데, 같은 batch 에 `streamingMessages` 를
 * 비우지 않으면 다음 render 의 `allMessages = [...messages, ...streamingMessages]`
 * 에 동일한 `stream-{uuid}` / `opt-{uuid}` / `tr-{uuid}` id 가 양쪽에 동시
 * 존재 → `useExternalMessageConverter` 가 assistant-ui `MessageRepository.link`
 * 호출 시 "A message with the same id already exists in the parent tree" throw.
 *
 * 본 테스트는 hook 안에서 부모처럼 `messages` 를 보관하고 `onMessagesCommit`
 * 에서 그대로 append 하는 실제 패턴을 재현해, 회귀가 발생하면 hook 자체가
 * render 중 throw 하도록 한다.
 */
import { renderHook, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { describe, expect, it, vi, beforeEach, type Mock } from 'vitest'
import type { Message, SSEEvent } from '@/lib/types'
import { sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import { mergeMessagesForRender, sameMessageSnapshot, useChatRuntime } from '../use-chat-runtime'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

const jotaiMock = vi.hoisted(() => {
  const setters = new WeakMap<object, Mock>()
  const useSetAtom = vi.fn((atom: object) => {
    let setter = setters.get(atom)
    if (!setter) {
      setter = vi.fn()
      setters.set(atom, setter)
    }
    return setter
  })

  return {
    getSetter: (atom: object) => setters.get(atom),
    useSetAtom,
  }
})

vi.mock('jotai', async () => {
  const actual = await vi.importActual<typeof import('jotai')>('jotai')
  return {
    ...actual,
    useSetAtom: jotaiMock.useSetAtom,
    useAtomValue: () => undefined,
  }
})

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
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

function makeStreamFn(customEvents: SSEEvent[]): (content: string) => AsyncGenerator<SSEEvent> {
  return async function* () {
    yield {
      event: 'message_start' as const,
      id: 'evt-start',
      data: { id: 'msg-1', role: 'assistant' },
    }
    let i = 0
    for (const ev of customEvents) {
      i += 1
      yield { ...ev, id: ev.id ?? `evt-${i}` }
    }
    yield {
      event: 'message_end' as const,
      id: 'evt-end',
      data: { content: '', usage: {} },
    }
  }
}

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

/** 빌더 / AssistantPanel / TestChatPanel 의 실제 패턴을 재현한 하네스. */
function useCommitHarness(events: SSEEvent[], onCommit?: (messages: Message[]) => void) {
  const [messages, setMessages] = useState<Message[]>([])
  const streamFn = useMemo(
    () =>
      makeStreamFn(events) as unknown as (
        content: string,
        signal: AbortSignal,
      ) => AsyncGenerator<SSEEvent>,
    [events],
  )
  const onMessagesCommit = useCallback(
    (msgs: Message[]) => {
      onCommit?.(msgs)
      setMessages((prev) => [...prev, ...msgs])
    },
    [onCommit],
  )
  const chat = useChatRuntime({ messages, streamFn, onMessagesCommit })
  return { ...chat, messages }
}

function useCommitHarnessWithStream(
  streamFn: (content: string, signal: AbortSignal) => AsyncGenerator<SSEEvent>,
  onCommit?: (messages: Message[]) => void,
) {
  const [messages, setMessages] = useState<Message[]>([])
  const onMessagesCommit = useCallback(
    (msgs: Message[]) => {
      onCommit?.(msgs)
      setMessages((prev) => [...prev, ...msgs])
    },
    [onCommit],
  )
  const chat = useChatRuntime({ messages, streamFn, onMessagesCommit })
  return { ...chat, messages }
}

function useUnstableEmptyMessagesHarness() {
  const [, setTick] = useState(0)
  const streamFn = useMemo(
    () =>
      async function* () {} as unknown as (
        content: string,
        signal: AbortSignal,
      ) => AsyncGenerator<SSEEvent>,
    [],
  )
  const chat = useChatRuntime({ messages: [], streamFn })
  return { ...chat, bump: () => setTick((value) => value + 1) }
}

beforeEach(() => {
  vi.clearAllMocks()
})

function message(id: string, role: Message['role'], content: string): Message {
  return {
    id,
    conversation_id: 'conversation-1',
    role,
    content,
    tool_calls: null,
    tool_call_id: null,
    created_at: '2026-06-04T00:00:00.000Z',
    feedback: null,
    attachments: null,
    usage: null,
    parent_id: null,
    branch_checkpoint_id: null,
    siblings: null,
    sibling_checkpoint_ids: null,
    branch_index: null,
    branch_total: null,
  }
}

describe('useChatRuntime — onMessagesCommit dedup', () => {
  it('refetch가 persisted assistant를 가져온 render에서 streaming turn을 즉시 숨긴다', () => {
    const previousMessages: Message[] = []
    const persistedUser = message('user-db', 'user', 'probe')
    const persistedAssistant = message('assistant-db', 'assistant', 'done')
    const optimisticUser = message('opt-user', 'user', 'probe')
    const streamingAssistant = message('stream-assistant', 'assistant', 'done')

    const merged = mergeMessagesForRender({
      messages: [persistedUser, persistedAssistant],
      previousMessages,
      streamingMessages: [optimisticUser, streamingAssistant],
      isRunning: false,
    })

    expect(merged.map((m) => m.id)).toEqual(['user-db', 'assistant-db'])
  })

  it('assistant row가 아직 persist되지 않은 refetch에서는 partial assistant를 보존한다', () => {
    const persistedUser = message('user-db', 'user', 'probe')
    const optimisticUser = message('opt-user', 'user', 'probe')
    const partialAssistant = message('stream-assistant', 'assistant', 'partial')

    const merged = mergeMessagesForRender({
      messages: [persistedUser],
      previousMessages: [],
      streamingMessages: [optimisticUser, partialAssistant],
      isRunning: false,
    })

    expect(merged.map((m) => m.id)).toEqual(['user-db', 'stream-assistant'])
  })

  it('부모가 빈 messages 배열을 새 참조로 넘겨도 render loop가 나지 않는다', () => {
    const { result } = renderHook(() => useUnstableEmptyMessagesHarness(), {
      wrapper: createWrapper(),
    })

    expect(() => {
      act(() => {
        result.current.bump()
      })
    }).not.toThrow()
  })

  it('stream 종료 후 부모가 commit 을 messages 에 append 해도 중복 id throw 없음', async () => {
    /**
     * 회귀 가드: 수정 전에는 finally 의 setState 가 같은 batch 에 처리되면서
     * 다음 render 에 `messages` 와 `streamingMessages` 가 동시에 `stream-{uuid}`
     * 를 담아 `useExternalMessageConverter` 가 throw 했다.
     * 수정 후에는 `setStreamingMessages([])` 가 `onMessagesCommit` 호출 직전에
     * 같은 batch 로 들어가 다음 render 의 `allMessages` 가 중복 없이 단일
     * source 로 유지된다.
     */
    const { result } = renderHook(
      () => useCommitHarness([{ event: 'content_delta', data: { content: 'Hello' } }]),
      { wrapper: createWrapper() },
    )

    // 회귀 시 이 호출 종료 후 다음 render 에서 throw.
    await act(async () => {
      await result.current.sendMessage('hi')
    })

    // 1) 부모 messages 에 commit 결과가 들어왔다 (user opt + assistant).
    const ids = result.current.messages.map((m) => m.id)
    expect(ids.length).toBeGreaterThanOrEqual(2)

    // 2) 중복 id 가 없다 — assistant-ui MessageRepository 의 핵심 contract.
    expect(new Set(ids).size).toBe(ids.length)

    // 3) assistant 메시지가 한 번만 존재한다.
    const assistantIds = result.current.messages
      .filter((m) => m.role === 'assistant')
      .map((m) => m.id)
    expect(assistantIds).toHaveLength(1)
  })

  it('tool_call 이 포함된 turn 도 중복 없이 commit 된다', async () => {
    const { result } = renderHook(
      () =>
        useCommitHarness([
          {
            event: 'tool_call_start',
            data: { tool_name: 'web_search', parameters: { q: 'foo' } },
          },
          {
            event: 'tool_call_result',
            data: { tool_name: 'web_search', result: 'bar' },
          },
          { event: 'content_delta', data: { content: 'done' } },
        ]),
      { wrapper: createWrapper() },
    )

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    const ids = result.current.messages.map((m) => m.id)
    expect(new Set(ids).size).toBe(ids.length)

    // assistant + tool result 가 각각 1건씩.
    const roleCount = result.current.messages.reduce<Record<string, number>>((acc, m) => {
      acc[m.role] = (acc[m.role] ?? 0) + 1
      return acc
    }, {})
    expect(roleCount.assistant).toBe(1)
    expect(roleCount.tool).toBe(1)
  })

  it('동일 도구 반복 호출 result를 tool_call_id 기준으로 매칭한다', async () => {
    const { result } = renderHook(
      () =>
        useCommitHarness([
          {
            event: 'tool_call_start',
            data: {
              tool_call_id: 'call-a',
              tool_name: 'web_search',
              parameters: { q: 'A' },
            },
          },
          {
            event: 'tool_call_start',
            data: {
              tool_call_id: 'call-b',
              tool_name: 'web_search',
              parameters: { q: 'B' },
            },
          },
          {
            event: 'tool_call_result',
            data: { tool_call_id: 'call-a', tool_name: 'web_search', result: 'result A' },
          },
          {
            event: 'tool_call_result',
            data: { tool_call_id: 'call-b', tool_name: 'web_search', result: 'result B' },
          },
        ]),
      { wrapper: createWrapper() },
    )

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    const assistant = result.current.messages.find((m) => m.role === 'assistant')
    expect(assistant?.tool_calls).toEqual([
      { id: 'call-a', name: 'web_search', args: { q: 'A' } },
      { id: 'call-b', name: 'web_search', args: { q: 'B' } },
    ])

    const resultByCallId = Object.fromEntries(
      result.current.messages
        .filter((m) => m.role === 'tool')
        .map((m) => [m.tool_call_id, m.content]),
    )
    expect(resultByCallId).toEqual({
      'call-a': 'result A',
      'call-b': 'result B',
    })
  })

  it('새 stream이 시작된 뒤 stale stream cleanup은 commit 하지 않는다', async () => {
    const firstYielded = deferred()
    const releaseFirst = deferred()
    const commitSpy = vi.fn()
    const streamFn = vi.fn((content: string) => {
      if (content === 'first') {
        return (async function* () {
          yield {
            event: 'content_delta' as const,
            id: 'first-delta',
            data: { content: 'old answer' },
          }
          firstYielded.resolve()
          await releaseFirst.promise
          yield {
            event: 'message_end' as const,
            id: 'first-end',
            data: { usage: {} },
          }
        })()
      }

      return (async function* () {
        yield {
          event: 'content_delta' as const,
          id: 'second-delta',
          data: { content: 'new answer' },
        }
        yield {
          event: 'message_end' as const,
          id: 'second-end',
          data: { usage: {} },
        }
      })()
    })

    const { result } = renderHook(
      () =>
        useCommitHarnessWithStream(
          streamFn as unknown as (content: string, signal: AbortSignal) => AsyncGenerator<SSEEvent>,
          commitSpy,
        ),
      { wrapper: createWrapper() },
    )

    let firstPromise!: Promise<void>
    await act(async () => {
      firstPromise = result.current.sendMessage('first')
      await firstYielded.promise
    })

    await act(async () => {
      await result.current.sendMessage('second')
    })

    await act(async () => {
      releaseFirst.resolve()
      await firstPromise
    })

    expect(commitSpy).toHaveBeenCalledTimes(1)
    expect(
      result.current.messages.filter((m) => m.role === 'assistant').map((m) => m.content),
    ).toEqual(['new answer'])
  })

  it('usage가 없는 content flush는 token usage atom을 반복 갱신하지 않는다', async () => {
    const { result } = renderHook(
      () =>
        useCommitHarness([
          { event: 'content_delta', data: { content: 'Hello' } },
          { event: 'content_delta', data: { content: ' world' } },
        ]),
      { wrapper: createWrapper() },
    )
    const tokenUsageSetter = jotaiMock.getSetter(sessionTokenUsageAtom)
    expect(tokenUsageSetter).toBeDefined()
    const callsBeforeStream = tokenUsageSetter?.mock.calls.length ?? 0

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    expect(tokenUsageSetter).toHaveBeenCalledTimes(callsBeforeStream)
  })

  it('messages refetch에서 branch/attachment/feedback/usage 변경도 snapshot 변경으로 본다', () => {
    const base: Message = {
      id: 'm1',
      conversation_id: 'c1',
      role: 'assistant',
      content: 'same text',
      tool_calls: null,
      tool_call_id: null,
      created_at: '2026-05-29T00:00:00Z',
      branch_index: 0,
      branch_total: 2,
      sibling_checkpoint_ids: ['ck1', 'ck2'],
      feedback: { rating: 'up' },
      usage: {
        prompt_tokens: 1,
        completion_tokens: 2,
        cache_creation_tokens: 0,
        cache_read_tokens: 0,
      },
    }

    expect(sameMessageSnapshot([base], [{ ...base }])).toBe(true)
    expect(sameMessageSnapshot([base], [{ ...base, branch_index: 1 }])).toBe(false)
    expect(
      sameMessageSnapshot(
        [base],
        [
          {
            ...base,
            attachments: [
              {
                id: 'att-1',
                filename: 'guide.png',
                mime_type: 'image/png',
                size_bytes: 12,
                url: '/api/conversations/c1/files/guide.png',
              },
            ],
          },
        ],
      ),
    ).toBe(false)
    expect(sameMessageSnapshot([base], [{ ...base, feedback: { rating: 'down' } }])).toBe(false)
    expect(
      sameMessageSnapshot(
        [base],
        [
          {
            ...base,
            usage: {
              prompt_tokens: 1,
              completion_tokens: 3,
              cache_creation_tokens: 0,
              cache_read_tokens: 0,
            },
          },
        ],
      ),
    ).toBe(false)
  })

  it('memory_saved 이벤트가 오면 memory query를 invalidate하고 toast를 표시한다', async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const Wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const streamFn = makeStreamFn([
      {
        event: 'memory_saved',
        data: {
          scope: 'user',
          content: '회의는 오후 3시 이후를 선호합니다.',
          id: 'memory-1',
        },
      },
    ])
    const { result } = renderHook(
      () =>
        useChatRuntime({
          messages: [],
          streamFn: streamFn as unknown as (
            content: string,
            signal: AbortSignal,
          ) => AsyncGenerator<SSEEvent>,
          onMessagesCommit: vi.fn(),
        }),
      { wrapper: Wrapper },
    )

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['memory'] })
    expect(toast.success).toHaveBeenCalledWith('savedToast')
  })
})
