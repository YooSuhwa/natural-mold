import { act, type ReactNode } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render } from '../../../../tests/test-utils'
import { ChatRuntimeSection } from '../chat-runtime-section'
import type { ConversationRun, Message, SSEEvent } from '@/lib/types'

const mocks = vi.hoisted(() => ({
  dictationAdapter: { listen: vi.fn() },
  createMoldyChatTools: vi.fn(() => ({})),
  resetDictationFailure: vi.fn(),
  assistantThreadProps: vi.fn(),
  useChatRuntime: vi.fn(),
  useMoldyLangGraphStream: vi.fn(),
}))

vi.mock('@/lib/chat/use-chat-runtime', () => ({
  useChatRuntime: mocks.useChatRuntime,
}))

vi.mock('@/lib/chat/langgraph-runtime/use-moldy-langgraph-stream', () => ({
  useMoldyLangGraphStream: mocks.useMoldyLangGraphStream,
}))

vi.mock('../use-browser-dictation', () => ({
  useBrowserDictation: () => ({
    adapter: mocks.dictationAdapter,
    availability: 'ready',
    resetFailure: mocks.resetDictationFailure,
  }),
}))

vi.mock('@assistant-ui/react', () => ({
  AuiConfig: (config: unknown) => config,
  Tools: ({ toolkit }: { toolkit: Record<string, unknown> }) => ({ toolkit }),
  AssistantRuntimeProvider: ({ runtime, children }: { runtime: string; children: ReactNode }) => (
    <div data-runtime={runtime} data-testid="assistant-runtime-provider">
      {children}
    </div>
  ),
  makeAssistantDataUI: (config: { name: string; render: unknown }) =>
    Object.assign(() => null, { unstable_data: config }),
}))

vi.mock('../assistant-thread', () => ({
  AssistantThread: (props: {
    activities?: readonly unknown[]
    conversationId?: string
    commandActions?: unknown
  }) => {
    mocks.assistantThreadProps(props)
    const { activities, conversationId } = props
    return (
      <div
        data-activity-count={activities?.length ?? 0}
        data-conversation-id={conversationId ?? 'draft'}
        data-testid="assistant-thread"
      />
    )
  },
}))

vi.mock('@/lib/chat/tool-ui-registry', () => ({
  ALL_TOOLKIT: {},
  createMoldyChatTools: mocks.createMoldyChatTools,
}))

const messages: Message[] = []
const activeRun: ConversationRun | null = null

async function* emptyStream(): AsyncGenerator<SSEEvent> {
  return
}

function langGraphStream(isLoading: boolean) {
  return {
    isLoading,
    subagents: new Map(),
  }
}

function renderSection(overrides: Partial<Parameters<typeof ChatRuntimeSection>[0]> = {}) {
  return render(
    <ChatRuntimeSection
      activeConversationId="conversation-1"
      activeRun={activeRun}
      agentId="agent-1"
      agentImageUrl={null}
      agentName="Agent"
      attachmentAdapter={undefined}
      emptyContent={<div />}
      feedbackAdapter={undefined}
      latestRun={null}
      messages={messages}
      modelName="Model"
      onRuntimeStatusChange={vi.fn()}
      onStreamEnd={vi.fn()}
      streamFn={emptyStream}
      totalCost={0}
      useLangGraphRuntime={false}
      user={null}
      {...overrides}
    />,
  )
}

describe('ChatRuntimeSection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.useChatRuntime.mockReturnValue({
      runtime: 'legacy-runtime',
      onResumeDecisions: vi.fn(),
      registerDecision: vi.fn(),
    })
    mocks.useMoldyLangGraphStream.mockReturnValue({
      assistantRuntime: 'langgraph-runtime',
      activities: [],
      stream: langGraphStream(false),
      onResumeDecisions: vi.fn(),
      registerDecision: vi.fn(),
    })
  })

  it('uses the legacy SSE runtime when the LangGraph flag is off', () => {
    renderSection()

    expect(mocks.useChatRuntime).toHaveBeenCalledOnce()
    expect(mocks.useMoldyLangGraphStream).not.toHaveBeenCalled()
    expect(document.querySelector('[data-runtime="legacy-runtime"]')).toBeInTheDocument()
  })

  it('passes the browser dictation adapter to the legacy runtime', () => {
    renderSection()

    expect(mocks.useChatRuntime).toHaveBeenCalledWith(
      expect.objectContaining({ dictationAdapter: mocks.dictationAdapter }),
    )
  })

  it('uses the LangGraph runtime when the flag is on for an existing conversation', () => {
    renderSection({ useLangGraphRuntime: true })

    expect(mocks.useChatRuntime).not.toHaveBeenCalled()
    expect(mocks.useMoldyLangGraphStream).toHaveBeenCalledWith(
      expect.objectContaining({
        agentId: 'agent-1',
        conversationId: 'conversation-1',
      }),
    )
    expect(document.querySelector('[data-runtime="langgraph-runtime"]')).toBeInTheDocument()
  })

  it('passes the browser dictation adapter to the LangGraph runtime', () => {
    renderSection({ useLangGraphRuntime: true })

    expect(mocks.useMoldyLangGraphStream).toHaveBeenCalledWith(
      expect.objectContaining({ dictationAdapter: mocks.dictationAdapter }),
    )
  })

  it('passes the draft commit callback to the LangGraph runtime', () => {
    const onBeforeNewMessage = vi.fn()
    const onNewMessageAccepted = vi.fn()

    renderSection({ onBeforeNewMessage, onNewMessageAccepted, useLangGraphRuntime: true })

    expect(mocks.useMoldyLangGraphStream).toHaveBeenCalledWith(
      expect.objectContaining({
        onBeforeSubmit: onBeforeNewMessage,
        onRunStartAccepted: expect.any(Function),
      }),
    )

    const runtimeOptions = mocks.useMoldyLangGraphStream.mock.calls[0]?.[0]
    act(() => runtimeOptions?.onRunStartAccepted?.())
    expect(onNewMessageAccepted).toHaveBeenCalledOnce()
  })

  it('offers retry only for the durable input bound to the latest failed run', async () => {
    const retryFailedInput = vi.fn(async () => undefined)
    const failedInput = {
      id: 'input-failed',
      conversation_id: 'conversation-1',
      run_id: 'run-failed',
      client_request_id: 'request-original',
      source: 'user',
      status: 'claimed' as const,
      priority: 0,
      position: 1,
      revision: 1,
      input_payload: {
        messages: [
          { role: 'user', content: [{ type: 'text', text: 'earlier successful input' }] },
          { role: 'assistant', content: [{ type: 'text', text: 'earlier successful answer' }] },
          { role: 'user', content: [{ type: 'text', text: 'latest failed input' }] },
        ],
      },
      resource_context: [],
      attachment_ids: [],
      checkpoint_id: null,
      claimed_at: '2026-09-06T00:00:00Z',
      created_at: '2026-09-06T00:00:00Z',
      updated_at: '2026-09-06T00:00:00Z',
    }
    const queueSnapshot = {
      queuePaused: false,
      items: [failedInput],
      lastOperation: { kind: 'idle' as const },
      reconciliationError: null,
    }
    mocks.useMoldyLangGraphStream.mockReturnValue({
      assistantRuntime: 'langgraph-runtime',
      activities: [],
      stream: langGraphStream(false),
      messageQueue: {
        subscribe: vi.fn(() => () => undefined),
        getSnapshot: vi.fn(() => queueSnapshot),
        refresh: vi.fn(async () => undefined),
      },
      retryFailedInput,
      onResumeDecisions: vi.fn(),
      registerDecision: vi.fn(),
      threadRunNotice: { id: 'run-failed', status: 'failed' },
    })

    renderSection({
      latestRun: {
        id: 'run-failed',
        conversation_id: 'conversation-1',
        agent_id: 'agent-1',
        parent_run_id: null,
        status: 'failed',
        source: 'user',
        worker_instance_id: null,
        interrupt_id: null,
        last_event_id: null,
        input_preview: 'latest failed input',
        error_code: 'MODEL_ERROR',
        error_message: 'failed',
        cancel_requested_at: null,
        started_at: null,
        heartbeat_at: null,
        completed_at: null,
        created_at: '2026-09-06T00:00:00Z',
        updated_at: '2026-09-06T00:00:00Z',
        metrics: null,
      },
      useLangGraphRuntime: true,
    })

    const lastProps = mocks.assistantThreadProps.mock.calls.at(-1)?.[0] as {
      commandActions?: {
        retryLastFailedInput?: {
          failedInputId: string
          execute: (failedInputId: string) => Promise<void>
        }
      }
    }
    const retry = lastProps.commandActions?.retryLastFailedInput
    expect(retry?.failedInputId).toBe('input-failed')
    await act(async () => retry?.execute('input-failed'))
    expect(retryFailedInput).toHaveBeenCalledExactlyOnceWith(failedInput)
    const afterRetry = mocks.assistantThreadProps.mock.calls.at(-1)?.[0] as {
      commandActions?: { retryLastFailedInput?: unknown }
    }
    expect(afterRetry.commandActions?.retryLastFailedInput).toBeUndefined()
  })

  it('passes LangGraph activities through to the assistant thread', () => {
    mocks.useMoldyLangGraphStream.mockReturnValue({
      assistantRuntime: 'langgraph-runtime',
      activities: [
        {
          id: 'activity-1',
          runId: 'run-1',
          kind: 'tool',
          status: 'running',
          title: 'web_search',
          namespace: [],
        },
      ],
      stream: langGraphStream(false),
      onResumeDecisions: vi.fn(),
      registerDecision: vi.fn(),
    })

    renderSection({ useLangGraphRuntime: true })

    expect(document.querySelector('[data-activity-count="1"]')).toBeInTheDocument()
  })

  it('falls back to legacy runtime for a draft conversation', () => {
    renderSection({ activeConversationId: null, useLangGraphRuntime: true })

    expect(mocks.useChatRuntime).toHaveBeenCalledWith(
      expect.objectContaining({
        conversationId: undefined,
      }),
    )
    expect(mocks.useMoldyLangGraphStream).not.toHaveBeenCalled()
  })

  it('reports LangGraph run status transitions and invalidates when a run settles', () => {
    const onRuntimeStatusChange = vi.fn()
    const onStreamEnd = vi.fn()
    mocks.useMoldyLangGraphStream.mockReturnValue({
      assistantRuntime: 'langgraph-runtime',
      activities: [],
      stream: langGraphStream(true),
      onResumeDecisions: vi.fn(),
      registerDecision: vi.fn(),
    })

    const { rerender } = renderSection({
      onRuntimeStatusChange,
      onStreamEnd,
      useLangGraphRuntime: true,
    })

    expect(onRuntimeStatusChange).toHaveBeenLastCalledWith('running')
    mocks.useMoldyLangGraphStream.mockReturnValue({
      assistantRuntime: 'langgraph-runtime',
      activities: [],
      stream: langGraphStream(false),
      onResumeDecisions: vi.fn(),
      registerDecision: vi.fn(),
    })
    rerender(
      <ChatRuntimeSection
        activeConversationId="conversation-1"
        activeRun={activeRun}
        agentId="agent-1"
        agentImageUrl={null}
        agentName="Agent"
        attachmentAdapter={undefined}
        emptyContent={<div />}
        feedbackAdapter={undefined}
        latestRun={null}
        messages={messages}
        modelName="Model"
        onRuntimeStatusChange={onRuntimeStatusChange}
        onStreamEnd={onStreamEnd}
        streamFn={emptyStream}
        totalCost={0}
        useLangGraphRuntime
        user={null}
      />,
    )

    expect(onRuntimeStatusChange).toHaveBeenLastCalledWith('idle')
    expect(onStreamEnd).toHaveBeenCalledWith(false)
  })
})
