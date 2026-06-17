import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ConversationRun, Message, SSEEvent } from '@/lib/types'
import { appendDurableCanceledNotice, useChatRuntime } from '../use-chat-runtime'

const mocks = vi.hoisted(() => ({
  setAtom: vi.fn(),
  translate: (key: string) => key,
}))

vi.mock('next-intl', () => ({
  useTranslations: () => mocks.translate,
}))

vi.mock('jotai', async () => {
  const actual = await vi.importActual<typeof import('jotai')>('jotai')
  return {
    ...actual,
    useSetAtom: () => mocks.setAtom,
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

function conversationRun(
  status: ConversationRun['status'],
  overrides?: Partial<ConversationRun>,
): ConversationRun {
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
    cancel_requested_at: '2026-06-11T00:00:01.000Z',
    started_at: '2026-06-11T00:00:00.000Z',
    heartbeat_at: null,
    completed_at: '2026-06-11T00:00:02.000Z',
    created_at: '2026-06-11T00:00:00.000Z',
    updated_at: '2026-06-11T00:00:02.000Z',
    ...overrides,
  }
}

function message(overrides: Pick<Message, 'id' | 'role' | 'content'> & Partial<Message>): Message {
  return {
    conversation_id: 'conversation-1',
    tool_calls: null,
    tool_call_id: null,
    created_at: '2026-06-11T00:00:00.000Z',
    ...overrides,
  }
}

async function* emptyStream(): AsyncGenerator<SSEEvent> {}

/** thread 렌더 목록의 메시지별 텍스트(텍스트 파트 연결)를 추출한다. */
function threadTexts(runtime: ReturnType<typeof useChatRuntime>['runtime']): string[] {
  return runtime.thread
    .getState()
    .messages.map((m) => m.content.map((part) => (part.type === 'text' ? part.text : '')).join(''))
}

const CANCELED_TEXT = '응답이 중단되었습니다'

describe('appendDurableCanceledNotice', () => {
  it('마지막 assistant 메시지 끝에 notice 텍스트를 덧붙이고 원본은 변경하지 않는다', () => {
    const original = [
      message({ id: 'm1', role: 'user', content: '질문' }),
      message({ id: 'm2', role: 'assistant', content: '부분 응답' }),
    ]

    const result = appendDurableCanceledNotice(original, CANCELED_TEXT, conversationRun('canceled'))

    expect(result).toHaveLength(2)
    expect(result[1].content).toBe(`부분 응답\n\n${CANCELED_TEXT}`)
    expect(original[1].content).toBe('부분 응답')
  })

  it('notice가 이미 붙어 있으면 중복으로 덧붙이지 않는다', () => {
    const original = [
      message({ id: 'm1', role: 'assistant', content: `부분 응답\n\n${CANCELED_TEXT}` }),
    ]

    const result = appendDurableCanceledNotice(original, CANCELED_TEXT, conversationRun('canceled'))

    expect(result).toHaveLength(1)
    expect(result[0].content).toBe(`부분 응답\n\n${CANCELED_TEXT}`)
  })

  it('빈 콘텐츠의 assistant 메시지에는 notice 텍스트만 채운다', () => {
    const original = [message({ id: 'm1', role: 'assistant', content: '' })]

    const result = appendDurableCanceledNotice(original, CANCELED_TEXT, conversationRun('canceled'))

    expect(result[0].content).toBe(CANCELED_TEXT)
  })

  it('마지막이 user 메시지면 run id로 키된 합성 assistant notice를 덧붙인다', () => {
    const original = [message({ id: 'm1', role: 'user', content: '질문' })]
    const run = conversationRun('canceled')

    const result = appendDurableCanceledNotice(original, CANCELED_TEXT, run)

    expect(result).toHaveLength(2)
    expect(result[1]).toMatchObject({
      id: 'canceled-run-1',
      role: 'assistant',
      content: CANCELED_TEXT,
      created_at: run.completed_at,
    })
  })

  it('합성 notice의 created_at은 completed_at → cancel_requested_at → updated_at 순으로 고른다', () => {
    const original = [message({ id: 'm1', role: 'user', content: '질문' })]
    const withoutCompleted = conversationRun('canceling', { completed_at: null })
    const withoutBoth = conversationRun('canceling', {
      completed_at: null,
      cancel_requested_at: null,
    })

    const fromCancelRequested = appendDurableCanceledNotice(
      original,
      CANCELED_TEXT,
      withoutCompleted,
    )
    const fromUpdated = appendDurableCanceledNotice(original, CANCELED_TEXT, withoutBoth)

    expect(fromCancelRequested[1].created_at).toBe(withoutCompleted.cancel_requested_at)
    expect(fromUpdated[1].created_at).toBe(withoutBoth.updated_at)
  })
})

describe('useChatRuntime durable canceled notice', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('latest_run이 canceled면 fetch 메시지만으로 notice를 렌더한다 (refetch 와이프/새로고침 시나리오)', async () => {
    const { result } = renderHook(
      () =>
        useChatRuntime({
          messages: [
            message({ id: 'm1', role: 'user', content: '질문' }),
            message({ id: 'm2', role: 'assistant', content: '부분 응답' }),
          ],
          streamFn: emptyStream,
          conversationId: 'conversation-1',
          latestRun: conversationRun('canceled'),
        }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => {
      const texts = threadTexts(result.current.runtime)
      // useTranslations mock이 key를 그대로 반환하므로 notice 텍스트는 'canceled'
      expect(texts[texts.length - 1]).toBe('부분 응답\n\ncanceled')
    })
  })

  it('출력 전에 취소되어 assistant 메시지가 없으면 합성 notice 메시지를 렌더한다', async () => {
    const { result } = renderHook(
      () =>
        useChatRuntime({
          messages: [message({ id: 'm1', role: 'user', content: '질문' })],
          streamFn: emptyStream,
          conversationId: 'conversation-1',
          latestRun: conversationRun('canceled'),
        }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => {
      const texts = threadTexts(result.current.runtime)
      expect(texts).toHaveLength(2)
      expect(texts[1]).toBe('canceled')
    })
  })

  it('canceling 상태도 notice를 렌더한다 (cancel 직후 worker 전이 전 refetch 케이스)', async () => {
    const { result } = renderHook(
      () =>
        useChatRuntime({
          messages: [
            message({ id: 'm1', role: 'user', content: '질문' }),
            message({ id: 'm2', role: 'assistant', content: '부분 응답' }),
          ],
          streamFn: emptyStream,
          conversationId: 'conversation-1',
          latestRun: conversationRun('canceling', { completed_at: null }),
        }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => {
      const texts = threadTexts(result.current.runtime)
      expect(texts[texts.length - 1]).toBe('부분 응답\n\ncanceled')
    })
  })

  it('latest_run이 completed면 notice를 렌더하지 않는다', async () => {
    const { result } = renderHook(
      () =>
        useChatRuntime({
          messages: [
            message({ id: 'm1', role: 'user', content: '질문' }),
            message({ id: 'm2', role: 'assistant', content: '완성된 응답' }),
          ],
          streamFn: emptyStream,
          conversationId: 'conversation-1',
          latestRun: conversationRun('completed', { cancel_requested_at: null }),
        }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => {
      const texts = threadTexts(result.current.runtime)
      expect(texts).toHaveLength(2)
      expect(texts[1]).toBe('완성된 응답')
    })
  })
})
