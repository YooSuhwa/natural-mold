/**
 * G-05 (plan 13.6): the legacy runtime (assistant panel, builder, legacy main chat)
 * receives the F-07 `auth_required` interrupt in its discriminated wire shape — live
 * over SSE and replayed by an active-run attach after a reconnect — and turns it into
 * the `moldy_auth_required` card, answered with `{type: 'respond', message, attempt}`.
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { streamResumeAttach } from '@/lib/sse/stream-resume-attach'
import type {
  AuthRequiredInterruptPayload,
  ConversationRun,
  Message,
  SSEEvent,
  StandardInterruptPayload,
} from '@/lib/types'
import { useChatRuntime } from '../use-chat-runtime'

const streamResumeDecisionsMock = vi.hoisted(() => vi.fn())
const setAtom = vi.hoisted(() => vi.fn()) // stable: an effect depends on the setter identity

vi.mock('@/lib/sse/stream-resume', () => ({ streamResumeDecisions: streamResumeDecisionsMock }))
vi.mock('@/lib/sse/stream-resume-attach', () => ({ streamResumeAttach: vi.fn() }))
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))
vi.mock('jotai', async () => {
  const actual = await vi.importActual<typeof import('jotai')>('jotai')
  return { ...actual, useSetAtom: () => setAtom, useAtomValue: () => undefined }
})
vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))

function createWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

const AUTH_WIRE: AuthRequiredInterruptPayload = {
  interrupt_id: 'auth-1',
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
  attempt: 2,
  allowed_decisions: ['respond'],
}

function stream(events: SSEEvent[]): () => AsyncGenerator<SSEEvent> {
  return async function* () {
    yield { event: 'message_start', id: 'e-start', data: { id: 'msg-1', role: 'assistant' } }
    for (const [index, event] of events.entries()) yield { ...event, id: `e-${index}` }
    yield { event: 'message_end', id: 'e-end', data: { content: '', usage: {} } }
  }
}

const interruptEvent: SSEEvent = { event: 'interrupt', data: AUTH_WIRE }
// Stable references: the hook re-runs effects when these change identity.
const NO_MESSAGES: Message[] = []
const interruptStream = stream([interruptEvent])
const emptyStream = stream([])

function authToolCall(messages: readonly Message[]) {
  return messages
    .flatMap((message) => message.tool_calls ?? [])
    .find((call) => call.name === 'moldy_auth_required')
}

function run(status: ConversationRun['status']): ConversationRun {
  return {
    id: 'run-1',
    conversation_id: 'conv-1',
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
    created_at: '2026-10-17T00:00:00.000Z',
    updated_at: '2026-10-17T00:00:00.000Z',
    metrics: null,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useChatRuntime auth_required (G-05)', () => {
  it('renders the live interrupt as the auth card and reports a native payload', async () => {
    const onStandardInterrupt = vi.fn<(payload: StandardInterruptPayload) => void>()
    const onMessagesCommit = vi.fn<(messages: Message[]) => void>()
    const { result } = renderHook(
      () =>
        useChatRuntime({
          messages: NO_MESSAGES,
          streamFn: interruptStream,
          conversationId: 'conv-1',
          onStandardInterrupt,
          onMessagesCommit,
        }),
      { wrapper: createWrapper() },
    )

    await act(async () => {
      await result.current.sendMessage('hi')
    })

    await waitFor(() => expect(onMessagesCommit).toHaveBeenCalled())
    expect(onStandardInterrupt).toHaveBeenCalledWith(
      expect.objectContaining({ interrupt_id: 'auth-1', native: 'auth_required' }),
    )
    const call = authToolCall(onMessagesCommit.mock.calls.at(-1)?.[0] ?? [])
    expect(call).toMatchObject({ id: 'auth-1:0#2', args: { attempt: 2, reason: 'expired' } })
  })

  it('answers the card with a respond decision carrying the attempt', async () => {
    streamResumeDecisionsMock.mockImplementation(stream([]))
    const { result } = renderHook(
      () =>
        useChatRuntime({
          messages: NO_MESSAGES,
          streamFn: interruptStream,
          conversationId: 'conv-1',
        }),
      { wrapper: createWrapper() },
    )
    await act(async () => {
      await result.current.sendMessage('hi')
    })

    const decision = { type: 'respond' as const, message: 'retry', attempt: 2 }
    await act(async () => {
      await result.current.registerDecision(0, decision, 'retry', 'auth-1')
    })

    expect(streamResumeDecisionsMock).toHaveBeenCalledWith(
      'conv-1',
      [decision],
      expect.any(AbortSignal),
      expect.any(Object),
    )
  })

  it('restores the card from the replay of an active-run attach (reconnect)', async () => {
    vi.mocked(streamResumeAttach).mockReturnValue(interruptStream())
    const onMessagesCommit = vi.fn<(messages: Message[]) => void>()
    const activeRun = run('running')
    renderHook(
      () =>
        useChatRuntime({
          messages: NO_MESSAGES,
          streamFn: emptyStream,
          conversationId: 'conv-1',
          activeRun,
          onMessagesCommit,
        }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => expect(onMessagesCommit).toHaveBeenCalled())
    const call = authToolCall(onMessagesCommit.mock.calls.at(-1)?.[0] ?? [])
    expect(call).toMatchObject({ id: 'auth-1:0#2', name: 'moldy_auth_required' })
  })

  it('answers a card restored by the messages API after a reload (no coordinator)', async () => {
    streamResumeDecisionsMock.mockImplementation(emptyStream)
    const restored: Message[] = [
      {
        id: 'm-1',
        conversation_id: 'conv-1',
        role: 'assistant',
        content: '',
        tool_calls: [
          {
            id: 'auth-1:0#2',
            name: 'moldy_auth_required',
            args: {
              attempt: 2,
              hitl_interrupt_id: 'auth-1',
              hitl_action_index: 0,
              hitl_total_actions: 1,
            },
          },
        ],
        tool_call_id: null,
        created_at: '2026-10-17T00:00:00.000Z',
      },
    ]
    const { result } = renderHook(
      () => useChatRuntime({ messages: restored, streamFn: emptyStream, conversationId: 'conv-1' }),
      { wrapper: createWrapper() },
    )
    const decision = { type: 'respond' as const, message: 'retry', attempt: 2 }

    await expect(result.current.registerDecision(0, decision, 'retry', 'other')).rejects.toThrow(
      'no longer active',
    )
    await act(async () => {
      await result.current.registerDecision(0, decision, 'retry', 'auth-1')
    })

    expect(streamResumeDecisionsMock).toHaveBeenCalledWith(
      'conv-1',
      [decision],
      expect.any(AbortSignal),
      expect.any(Object),
    )
  })
})
