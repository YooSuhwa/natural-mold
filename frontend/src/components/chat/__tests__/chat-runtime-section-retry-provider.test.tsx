import { useCallback, useMemo, type ReactNode } from 'react'
import {
  MessageProvider,
  fromThreadMessageLike,
  useLocalRuntime,
  type AssistantRuntime,
} from '@assistant-ui/react'
import { act, fireEvent } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { RetryButton } from '@/components/chat/assistant-message-actions'
import { ChatRuntimeSection } from '@/components/chat/chat-runtime-section'
import type { ConversationRunInput } from '@/lib/api/conversation-run-inputs'
import type { ThreadRunNotice } from '@/lib/chat/langgraph-runtime/stream-thread-state-projection'
import type { ConversationRun, Message, SSEEvent } from '@/lib/types'
import { render, screen } from '../../../../tests/test-utils'

const mocks = vi.hoisted(() => ({
  retryFailedInput: vi.fn<(input: ConversationRunInput) => Promise<void>>(),
  useMoldyLangGraphStream: vi.fn(),
}))

vi.mock('@assistant-ui/react', async () => {
  const actual = await vi.importActual<typeof import('@assistant-ui/react')>('@assistant-ui/react')
  return {
    ...actual,
    ActionBarPrimitive: {
      ...actual.ActionBarPrimitive,
      Reload: ({ children, ...props }: { readonly children: ReactNode }) => (
        <button type="button" {...props}>
          {children}
        </button>
      ),
    },
  }
})

vi.mock('@/lib/chat/langgraph-runtime/use-moldy-langgraph-stream', () => ({
  useMoldyLangGraphStream: mocks.useMoldyLangGraphStream,
}))

vi.mock('@/components/chat/use-browser-dictation', () => ({
  useBrowserDictation: () => ({
    adapter: undefined,
    availability: 'unsupported',
    resetFailure: vi.fn(),
  }),
}))

vi.mock('@/lib/chat/data-ui', () => ({ ALL_DATA_UI: {} }))

vi.mock('@/lib/chat/tool-ui-registry', async () => {
  const { Tools } =
    await vi.importActual<typeof import('@assistant-ui/react')>('@assistant-ui/react')
  return {
    ALL_TOOLKIT: {},
    createMoldyChatTools: () => Tools({ toolkit: {} }),
  }
})

vi.mock('@/lib/chat/langgraph-runtime/subagent-runtime', () => ({
  SubagentRuntimeProvider: ({ children }: { readonly children: ReactNode }) => children,
  usePublishSubagentRuntime: () => {},
}))

vi.mock('../assistant-thread', () => ({
  AssistantThread: () => {
    const message = fromThreadMessageLike(
      {
        id: 'moldy-failed-run-current',
        role: 'assistant',
        content: 'failed',
      },
      'moldy-failed-run-current',
      { type: 'complete', reason: 'stop' },
    )
    return (
      <MessageProvider message={message} index={0}>
        <RetryButton />
      </MessageProvider>
    )
  },
}))

const FAILED_INPUT: ConversationRunInput = {
  id: 'input-current',
  conversation_id: 'conversation-1',
  run_id: 'run-current',
  client_request_id: 'request-original',
  source: 'chat',
  status: 'failed',
  priority: 0,
  position: 1,
  revision: 1,
  input_payload: { messages: [] },
  resource_context: [],
  attachment_ids: [],
  checkpoint_id: null,
  claimed_at: null,
  created_at: '2026-09-07T00:00:00Z',
  updated_at: '2026-09-07T00:00:00Z',
}

const FAILED_RUN: ConversationRun = {
  id: 'run-current',
  conversation_id: 'conversation-1',
  agent_id: 'agent-1',
  parent_run_id: null,
  status: 'failed',
  source: 'chat',
  worker_instance_id: null,
  interrupt_id: null,
  last_event_id: null,
  input_preview: 'failed input',
  error_code: 'MODEL_ERROR',
  error_message: 'failed',
  cancel_requested_at: null,
  started_at: null,
  heartbeat_at: null,
  completed_at: null,
  created_at: '2026-09-07T00:00:00Z',
  updated_at: '2026-09-07T00:00:00Z',
  metrics: null,
}

const RUNTIME_FAILED_NOTICE: ThreadRunNotice = {
  id: 'run-current',
  status: 'failed',
  errorMessage: 'failed',
}

const RUNTIME_CANCELED_NOTICE: ThreadRunNotice = {
  id: 'run-current',
  status: 'canceled',
}

const STALE_PAGE_FAILED_RUN: ConversationRun = {
  ...FAILED_RUN,
  id: 'run-page-stale',
}

const EMPTY_MESSAGES: Message[] = []
const PENDING_INPUT: ConversationRunInput = { ...FAILED_INPUT, status: 'pending' }

async function* emptyStream(): AsyncGenerator<SSEEvent> {
  return
}

function RetryProviderHarness({
  input = FAILED_INPUT,
  latestRun = FAILED_RUN,
  terminalNotice = null,
  isLoading = false,
}: {
  readonly input?: ConversationRunInput
  readonly latestRun?: ConversationRun | null
  readonly terminalNotice?: ThreadRunNotice | null
  readonly isLoading?: boolean
}) {
  const onNew = useCallback(async () => ({ content: [] }), [])
  const runtime = useLocalRuntime({ run: onNew })
  const queueSnapshot = useMemo(
    () => ({
      queuePaused: false,
      items: [input],
      lastOperation: { kind: 'idle' as const },
      reconciliationError: null,
      rejectedSubmission: null,
    }),
    [input],
  )
  mocks.useMoldyLangGraphStream.mockReturnValue({
    assistantRuntime: runtime as AssistantRuntime,
    activities: [],
    stream: { isLoading, subagents: new Map() },
    messageQueue: {
      subscribe: () => () => undefined,
      getSnapshot: () => queueSnapshot,
      refresh: async () => undefined,
    },
    retryFailedInput: mocks.retryFailedInput,
    onResumeDecisions: vi.fn(),
    registerDecision: vi.fn(),
    threadRunNotice: terminalNotice,
  })

  return (
    <ChatRuntimeSection
      activeConversationId="conversation-1"
      activeRun={null}
      agentId="agent-1"
      emptyContent={null}
      latestRun={latestRun}
      messages={EMPTY_MESSAGES}
      onRuntimeStatusChange={vi.fn()}
      onStreamEnd={vi.fn()}
      streamFn={emptyStream}
      useLangGraphRuntime
    />
  )
}

describe('ChatRuntimeSection failed-message recovery provider', () => {
  beforeEach(() => {
    mocks.retryFailedInput.mockReset()
  })

  it('uses the real public AUI message context to invoke the current failed input once', async () => {
    let resolveRetry: (() => void) | undefined
    mocks.retryFailedInput.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveRetry = resolve
        }),
    )

    render(<RetryProviderHarness terminalNotice={RUNTIME_FAILED_NOTICE} />)
    const retryButton = screen.getByRole('button', { name: '다시 시도' })

    await act(async () => {
      fireEvent.click(retryButton)
      fireEvent.click(retryButton)
    })

    expect(mocks.retryFailedInput).toHaveBeenCalledExactlyOnceWith(FAILED_INPUT)
    await act(async () => resolveRetry?.())
    expect(screen.queryByRole('button', { name: '다시 시도' })).not.toBeInTheDocument()
  })

  it('uses the hydrated terminal failure over a stale page run for the matching durable input', async () => {
    mocks.retryFailedInput.mockResolvedValue(undefined)

    render(
      <RetryProviderHarness
        latestRun={STALE_PAGE_FAILED_RUN}
        terminalNotice={RUNTIME_FAILED_NOTICE}
        isLoading
      />,
    )

    const retryButton = screen.getByRole('button', { name: '다시 시도' })
    await act(async () => {
      fireEvent.click(retryButton)
      fireEvent.click(retryButton)
    })

    expect(mocks.retryFailedInput).toHaveBeenCalledExactlyOnceWith(FAILED_INPUT)
  })

  it('keeps a stale failed bubble unavailable after the current terminal notice is canceled', () => {
    render(<RetryProviderHarness terminalNotice={RUNTIME_CANCELED_NOTICE} />)

    expect(screen.queryByRole('button', { name: '다시 시도' })).not.toBeInTheDocument()
  })

  it('keeps a stale failed bubble unavailable while a newer run is active without a terminal notice', () => {
    render(<RetryProviderHarness isLoading terminalNotice={null} />)

    expect(screen.queryByRole('button', { name: '다시 시도' })).not.toBeInTheDocument()
    expect(mocks.retryFailedInput).not.toHaveBeenCalled()
  })

  it('does not expose generic reload when this recovery-aware main surface has no failed run', () => {
    render(<RetryProviderHarness latestRun={null} terminalNotice={null} />)

    expect(screen.queryByRole('button', { name: '다시 시도' })).not.toBeInTheDocument()
  })

  it('keeps pending and rejected retries unavailable without falling back to generic reload', async () => {
    const { rerender } = render(
      <RetryProviderHarness input={PENDING_INPUT} terminalNotice={RUNTIME_FAILED_NOTICE} />,
    )

    expect(screen.queryByRole('button', { name: '다시 시도' })).not.toBeInTheDocument()

    mocks.retryFailedInput.mockRejectedValueOnce(new Error('retry rejected'))
    rerender(<RetryProviderHarness terminalNotice={RUNTIME_FAILED_NOTICE} />)
    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }))

    await act(async () => undefined)
    expect(mocks.retryFailedInput).toHaveBeenCalledExactlyOnceWith(FAILED_INPUT)
    expect(screen.queryByRole('button', { name: '다시 시도' })).not.toBeInTheDocument()
  })
})
