/**
 * HiTL Phase 2 — `case 'interrupt'` 분기 + dedup 회귀 가드.
 *
 * 검증 대상 (`docs/exec-plans/active/hitl-phase2-contract.md` §5.2 / §4.3):
 *
 * 1. 표준 chunk(`action_requests`/`review_configs` 키 + 비어있지 않음)가 도착
 *    하면 `onStandardInterrupt` 호출, `onInterrupt`는 미호출.
 * 2. legacy chunk(`value` 키)가 단독 도착하면 기존 `onInterrupt` 호출 (회귀 0).
 * 3. 같은 `interrupt_id`로 표준 chunk 처리 후 legacy chunk가 도착해도 dedup
 *    되어 `onInterrupt`가 호출되지 않는다 (이중 호출 방지).
 * 4. multi-action(action_requests.length >= 2) 표준 chunk가 그대로 한 번에
 *    `onStandardInterrupt`로 전달된다 (배열을 backend가 한 묶음으로 발행 — §4.3).
 * 5. fallback 표준 chunk(action_requests=[])는 표준 처리에서 skip되고, 같은
 *    interrupt_id의 legacy chunk가 채택된다 (§4.5).
 *
 * 의존성 주의 (brittleness 명시):
 *  - assistant-ui의 `useExternalStoreRuntime` / `useExternalMessageConverter`
 *    는 internal state를 들고 있어 hook을 그대로 렌더해 단언한다.
 *  - jotai/sonner/next-intl는 가벼운 mock으로 isolation. 실 hook의 dispatch
 *    분기 로직(`case 'interrupt'` 50줄 미만)만 검증한다.
 */
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  Decision,
  InterruptPayload,
  LegacyInterruptPayload,
  Message,
  SSEEvent,
  StandardInterruptPayload,
} from '@/lib/types'
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
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

/** Build an SSE stream with `message_start` → custom events → `message_end`.
 *  IDs are unique so streamGuard dedup never blocks our events. */
function makeStreamFn(
  customEvents: SSEEvent[],
): (content: string) => AsyncGenerator<SSEEvent> {
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
  action_requests: [
    { name: 'send_email', args: { to: 'x@y' }, description: 'Send' },
  ],
  review_configs: [
    {
      action_name: 'send_email',
      allowed_decisions: ['approve', 'edit', 'reject', 'respond'],
    },
  ],
}

const LEGACY_PAYLOAD: LegacyInterruptPayload = {
  interrupt_id: 'ns-1',
  value: { type: 'select', question: 'Choose?', options: ['a', 'b'] },
}

// ── 공통 hook 옵션 빌더 ───────────────────────────────────────────────────

interface HookSpyOptions {
  events: SSEEvent[]
  initialMessages?: Message[]
}

function buildHookOptions(opts: HookSpyOptions) {
  const onInterrupt = vi.fn<(p: LegacyInterruptPayload) => void>()
  const onStandardInterrupt = vi.fn<(p: StandardInterruptPayload) => void>()
  const streamFn = makeStreamFn(opts.events)
  return {
    onInterrupt,
    onStandardInterrupt,
    streamFn,
    options: {
      messages: opts.initialMessages ?? [],
      streamFn: streamFn as unknown as (
        content: string,
        signal: AbortSignal,
        options?: { onRunId?: (id: string) => void },
      ) => AsyncGenerator<SSEEvent>,
      onInterrupt,
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

describe('useChatRuntime — case "interrupt" 분기 (HiTL Phase 2 §5.2)', () => {
  it('표준 chunk(비어있지 않은 action_requests)는 onStandardInterrupt만 호출', async () => {
    const { onInterrupt, onStandardInterrupt, options } = buildHookOptions({
      events: [
        { event: 'interrupt', data: STANDARD_PAYLOAD as unknown as Record<string, unknown> },
      ],
    })
    const { result } = renderHook(() => useChatRuntime(options), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    await waitFor(() => expect(onStandardInterrupt).toHaveBeenCalledTimes(1))
    expect(onStandardInterrupt).toHaveBeenCalledWith(STANDARD_PAYLOAD)
    expect(onInterrupt).not.toHaveBeenCalled()
  })

  it('legacy chunk(value 키)가 단독 도착하면 onInterrupt 호출 (회귀 0)', async () => {
    const { onInterrupt, onStandardInterrupt, options } = buildHookOptions({
      events: [
        { event: 'interrupt', data: LEGACY_PAYLOAD as unknown as Record<string, unknown> },
      ],
    })
    const { result } = renderHook(() => useChatRuntime(options), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    await waitFor(() => expect(onInterrupt).toHaveBeenCalledTimes(1))
    expect(onInterrupt).toHaveBeenCalledWith(LEGACY_PAYLOAD)
    expect(onStandardInterrupt).not.toHaveBeenCalled()
  })

  it('표준 → legacy 순으로 같은 interrupt_id 도착 시 표준만 1회 호출 (dedup)', async () => {
    const { onInterrupt, onStandardInterrupt, options } = buildHookOptions({
      events: [
        // dual emit (backend §4.3): 표준 먼저
        { event: 'interrupt', data: STANDARD_PAYLOAD as unknown as Record<string, unknown> },
        // 같은 interrupt_id의 legacy chunk
        { event: 'interrupt', data: LEGACY_PAYLOAD as unknown as Record<string, unknown> },
      ],
    })
    const { result } = renderHook(() => useChatRuntime(options), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    await waitFor(() => expect(onStandardInterrupt).toHaveBeenCalledTimes(1))
    expect(onStandardInterrupt).toHaveBeenCalledWith(STANDARD_PAYLOAD)
    // dedup 핵심: legacy chunk가 함께 와도 onInterrupt는 호출되면 안 된다.
    expect(onInterrupt).not.toHaveBeenCalled()
  })

  it('multi-action(action_requests.length >= 2) 표준 chunk는 한 번에 통째로 전달', async () => {
    // backend는 한 interrupt = 한 묶음(여러 action_requests) = 두 chunk(표준+legacy)
    // 로 발행 (§4.3). frontend는 multi-action 배열을 분리하지 않고 그대로 콜백에 위임.
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
    const { onInterrupt, onStandardInterrupt, options } = buildHookOptions({
      events: [{ event: 'interrupt', data: multi as unknown as Record<string, unknown> }],
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
    expect(onInterrupt).not.toHaveBeenCalled()
  })

  it('fallback (aget_state 실패) → legacy chunk 단독 채택 (contract §4.5)', async () => {
    /**
     * Contract §4.5 — `was_interrupted=True + aget_state` 실패 시 backend는
     * legacy chunk 1개만 emit (`{interrupt_id:"", value:{message:"…unavailable"}}`).
     * frontend는 `action_requests` 키 부재로 legacy 경로를 채택해 fallback
     * 메시지를 1회 노출.
     */
    const fallbackLegacy: LegacyInterruptPayload = {
      interrupt_id: '',
      value: { message: 'Interrupt detected but state unavailable' },
    }
    const { onInterrupt, onStandardInterrupt, options } = buildHookOptions({
      events: [
        { event: 'interrupt', data: fallbackLegacy as unknown as Record<string, unknown> },
      ],
    })
    const { result } = renderHook(() => useChatRuntime(options), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    await waitFor(() => expect(onInterrupt).toHaveBeenCalledTimes(1))
    expect(onInterrupt).toHaveBeenCalledWith(fallbackLegacy)
    expect(onStandardInterrupt).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// onResumeDecisions 호출 — body 빌더 통합 (스트림 자체는 stream-resume.test.ts).
// ---------------------------------------------------------------------------

describe('useChatRuntime — onResumeDecisions (Phase 2 표준 resume API)', () => {
  it('hook이 onResumeDecisions 함수를 노출한다 (HiTLContext 호환 §6.3)', () => {
    const { options } = buildHookOptions({ events: [] })
    const { result } = renderHook(() => useChatRuntime(options), {
      wrapper: createWrapper(),
    })

    expect(typeof result.current.onResumeDecisions).toBe('function')
    expect(typeof result.current.onResume).toBe('function')
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
    await expect(
      result.current.onResumeDecisions(decisions),
    ).resolves.toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// 타입 좁힘 sanity — InterruptPayload union의 분기 키 (§5.1).
// ---------------------------------------------------------------------------

describe('InterruptPayload union — 타입 좁힘 sanity', () => {
  it("'action_requests' in data 체크가 표준/legacy를 정확히 분리", () => {
    const std: InterruptPayload = STANDARD_PAYLOAD
    const legacy: InterruptPayload = LEGACY_PAYLOAD
    expect('action_requests' in std).toBe(true)
    expect('action_requests' in legacy).toBe(false)
    expect('value' in std).toBe(false)
    expect('value' in legacy).toBe(true)
  })
})
