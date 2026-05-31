/**
 * `case 'interrupt'` 표준 단독 경로 + multi-action / fallback empty 표준 chunk
 * 가 그대로 `onStandardInterrupt`로 전달되는지 검증.
 *
 * Brittleness: assistant-ui의 `useExternalStoreRuntime` /
 * `useExternalMessageConverter`는 internal state를 가지므로 hook을 그대로 렌더한다.
 * jotai/sonner/next-intl는 가벼운 mock으로 격리.
 */
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { toast } from 'sonner'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Decision, Message, SSEEvent, StandardInterruptPayload } from '@/lib/types'
import { useChatRuntime } from '../use-chat-runtime'

// ── 가벼운 mocks ──────────────────────────────────────────────────────────
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
  toast: { error: vi.fn(), success: vi.fn() },
}))

// ── helpers ──────────────────────────────────────────────────────────────

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

/** Build an SSE stream with `message_start` → custom events → `message_end`.
 *  IDs are unique so streamGuard dedup never blocks our events. */
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

const STANDARD_PAYLOAD: StandardInterruptPayload = {
  interrupt_id: 'ns-1',
  action_requests: [{ name: 'send_email', args: { to: 'x@y' }, description: 'Send' }],
  review_configs: [
    {
      action_name: 'send_email',
      allowed_decisions: ['approve', 'edit', 'reject', 'respond'],
    },
  ],
}

// ── 공통 hook 옵션 빌더 ───────────────────────────────────────────────────

interface HookSpyOptions {
  events: SSEEvent[]
  initialMessages?: Message[]
}

function buildHookOptions(opts: HookSpyOptions) {
  const onStandardInterrupt = vi.fn<(p: StandardInterruptPayload) => void>()
  const streamFn = makeStreamFn(opts.events)
  return {
    onStandardInterrupt,
    streamFn,
    options: {
      messages: opts.initialMessages ?? [],
      streamFn: streamFn as unknown as (
        content: string,
        signal: AbortSignal,
        options?: { onRunId?: (id: string) => void },
      ) => AsyncGenerator<SSEEvent>,
      onStandardInterrupt,
      conversationId: 'conv-1',
    },
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('useChatRuntime — case "interrupt" 표준 경로', () => {
  it('표준 chunk가 도착하면 onStandardInterrupt가 1회 호출된다', async () => {
    const { onStandardInterrupt, options } = buildHookOptions({
      events: [{ event: 'interrupt', data: STANDARD_PAYLOAD }],
    })
    const { result } = renderHook(() => useChatRuntime(options), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    await waitFor(() => expect(onStandardInterrupt).toHaveBeenCalledTimes(1))
    expect(onStandardInterrupt).toHaveBeenCalledWith(STANDARD_PAYLOAD)
  })

  it('표준 chunk가 도착하면 내부 tool UI 호출도 합성한다', async () => {
    const { options } = buildHookOptions({
      events: [{ event: 'interrupt', data: STANDARD_PAYLOAD }],
    })
    const onMessagesCommit = vi.fn<(messages: Message[]) => void>()
    const { result } = renderHook(
      () =>
        useChatRuntime({
          ...options,
          onStandardInterrupt: undefined,
          onMessagesCommit,
        }),
      {
        wrapper: createWrapper(),
      },
    )

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    await waitFor(() => expect(onMessagesCommit).toHaveBeenCalled())
    const committed = onMessagesCommit.mock.calls.at(-1)?.[0] ?? []
    const assistant = committed.find((message) => message.role === 'assistant')
    expect(assistant?.tool_calls).toEqual([
      {
        id: 'ns-1:0',
        name: 'request_approval',
        args: {
          tool_name: 'send_email',
          tool_args: { to: 'x@y' },
          description: 'Send',
          approval_id: 'ns-1:0',
          allowed_decisions: ['approve', 'edit', 'reject', 'respond'],
          hitl_interrupt_id: 'ns-1',
          hitl_action_index: 0,
          hitl_total_actions: 1,
        },
      },
    ])
  })

  it('이미 stream에 나온 ask_user tool call에는 interrupt metadata만 병합한다', async () => {
    const questionFlowPayload: StandardInterruptPayload = {
      interrupt_id: 'ns-ask-user',
      action_requests: [
        {
          name: 'ask_user',
          args: {
            mode: 'question_flow',
            title: '에이전트 설정 확인',
            questions: [
              {
                id: 'agent_name',
                label: '에이전트 이름',
                type: 'single_select',
                options: [{ id: 'research', label: '리서치 에이전트' }],
                required: true,
              },
            ],
          },
        },
      ],
      review_configs: [{ action_name: 'ask_user', allowed_decisions: ['respond'] }],
    }
    const { options } = buildHookOptions({
      events: [
        {
          event: 'tool_call_start',
          data: {
            tool_name: 'ask_user',
            parameters: questionFlowPayload.action_requests[0].args,
          },
        },
        { event: 'interrupt', data: questionFlowPayload },
      ],
    })
    const onMessagesCommit = vi.fn<(messages: Message[]) => void>()
    const { result } = renderHook(
      () =>
        useChatRuntime({
          ...options,
          onMessagesCommit,
        }),
      {
        wrapper: createWrapper(),
      },
    )

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    await waitFor(() => expect(onMessagesCommit).toHaveBeenCalled())
    const committed = onMessagesCommit.mock.calls.at(-1)?.[0] ?? []
    const assistant = committed.find((message) => message.role === 'assistant')
    expect(assistant?.tool_calls).toHaveLength(1)
    expect(assistant?.tool_calls?.[0]).toMatchObject({
      name: 'ask_user',
      args: {
        mode: 'question_flow',
        title: '에이전트 설정 확인',
        approval_id: 'ns-ask-user:0',
        allowed_decisions: ['respond'],
        hitl_interrupt_id: 'ns-ask-user',
        hitl_action_index: 0,
        hitl_total_actions: 1,
      },
    })
  })

  it('multi-action(action_requests.length >= 2) 표준 chunk는 한 번에 통째로 전달', async () => {
    // backend는 한 interrupt = 한 묶음(여러 action_requests) 으로 발행 (§4.3).
    // frontend는 multi-action 배열을 분리하지 않고 그대로 콜백에 위임.
    const multi: StandardInterruptPayload = {
      interrupt_id: 'ns-multi',
      action_requests: [
        { name: 'send_email', args: { to: 'a@b' } },
        { name: 'delete_record', args: { id: 42 } },
        { name: 'create_event', args: { title: 'meet' } },
      ],
      review_configs: [
        { action_name: 'send_email', allowed_decisions: ['approve', 'reject'] },
        { action_name: 'delete_record', allowed_decisions: ['approve', 'reject'] },
        { action_name: 'create_event', allowed_decisions: ['approve', 'edit', 'reject'] },
      ],
    }
    const { onStandardInterrupt, options } = buildHookOptions({
      events: [{ event: 'interrupt', data: multi }],
    })
    const { result } = renderHook(() => useChatRuntime(options), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    await waitFor(() => expect(onStandardInterrupt).toHaveBeenCalledTimes(1))
    const arg = onStandardInterrupt.mock.calls[0][0]
    expect(arg.action_requests).toHaveLength(3)
    expect(arg.review_configs).toHaveLength(3)
  })

  it('fallback 표준 chunk(action_requests=[]) 는 toast 안내 + onStandardInterrupt 미호출', async () => {
    /**
     * Backend fallback (``aget_state`` 실패): 빈 표준 chunk
     * ``{interrupt_id: "", action_requests: [], review_configs: []}`` 1회 emit.
     * turn 이 silent 하게 갇히지 않도록 hook 이 toast 로 사용자에게 안내하고
     * ``onStandardInterrupt`` 는 호출하지 않는다(액션 카드 렌더 의미 없음).
     */
    vi.mocked(toast.error).mockClear()
    const fallbackStd: StandardInterruptPayload = {
      interrupt_id: '',
      action_requests: [],
      review_configs: [],
    }
    const { onStandardInterrupt, options } = buildHookOptions({
      events: [{ event: 'interrupt', data: fallbackStd }],
    })
    const { result } = renderHook(() => useChatRuntime(options), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    expect(onStandardInterrupt).not.toHaveBeenCalled()
  })

  it('SSE error event 도달 시 toast.error 1회 호출 (silent fail 가드)', async () => {
    /** ``setStreamError`` 는 setter-only state 라 UI 미노출.
     * Backend 의 SSE ``error`` event (예: OpenAI 404, model not found) 가
     * 사용자 화면에 silent 하게 사라지지 않도록 toast 강제 호출. */
    vi.mocked(toast.error).mockClear()
    const { options } = buildHookOptions({
      events: [
        {
          event: 'error',
          data: { message: 'Error code: 404' },
        },
      ],
    })
    const { result } = renderHook(() => useChatRuntime(options), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    expect(toast.error).toHaveBeenCalledWith(
      'Error code: 404',
      expect.objectContaining({ id: 'chat-stream-error' }),
    )
  })

  it('한 stream 다중 error event 시 dedup id 로 sonner 가 토스트 교체', async () => {
    /**
     * Backend 가 한 turn 안에 ``error`` SSE event 를 여러 개 emit (e.g. tool
     * 단계마다 fail 누적) 하면 이전 구현은 토스트가 스택 → 화면 가림. 모든
     * 호출이 동일 id (``chat-stream-error``) 를 부여해 sonner 가 교체하도록
     * 보장 (시각적 dedup). 호출 횟수가 아닌 id 일관성을 회귀 가드.
     */
    vi.mocked(toast.error).mockClear()
    const { options } = buildHookOptions({
      events: [
        { event: 'error', data: { message: 'first' } },
        { event: 'error', data: { message: 'second' } },
        { event: 'error', data: { message: 'third' } },
      ],
    })
    const { result } = renderHook(() => useChatRuntime(options), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(3))
    // 모든 호출이 같은 dedup id 보유 — sonner 가 자동 교체
    const calls = vi.mocked(toast.error).mock.calls
    for (const call of calls) {
      expect(call[1]).toEqual(expect.objectContaining({ id: 'chat-stream-error' }))
    }
  })
})

// ---------------------------------------------------------------------------
// onResumeDecisions 호출 — body 빌더 통합 (스트림 자체는 stream-resume.test.ts).
// ---------------------------------------------------------------------------

describe('useChatRuntime — onResumeDecisions', () => {
  it('hook이 onResumeDecisions 함수를 노출한다 (HiTLContext 호환 §6.3)', () => {
    const { options } = buildHookOptions({ events: [] })
    const { result } = renderHook(() => useChatRuntime(options), {
      wrapper: createWrapper(),
    })

    expect(typeof result.current.onResumeDecisions).toBe('function')
    expect(typeof result.current.registerDecision).toBe('function')
    // legacy `onResume`은 더 이상 노출되지 않는다 (회귀 가드).
    expect('onResume' in result.current).toBe(false)
  })

  it('conversationId가 없으면 onResumeDecisions는 noop으로 동작', async () => {
    // builder v3 등 conversationId 없는 컨텍스트 — resume 송신 안 함.
    const { options } = buildHookOptions({ events: [] })
    const noConvOptions = { ...options, conversationId: undefined }
    const { result } = renderHook(() => useChatRuntime(noConvOptions), {
      wrapper: createWrapper(),
    })

    const decisions: Decision[] = [{ type: 'approve' }]
    // throw 없이 즉시 resolve.
    await expect(result.current.onResumeDecisions(decisions)).resolves.toBeUndefined()
  })
})
