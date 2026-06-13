import type { ReactNode } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render } from '../../../../tests/test-utils'
import { ChatRuntimeSection } from '../chat-runtime-section'
import type { ConversationRun, Message, SSEEvent } from '@/lib/types'

const mocks = vi.hoisted(() => ({
  useChatRuntime: vi.fn(),
  useMoldyLangGraphStream: vi.fn(),
}))

vi.mock('@/lib/chat/use-chat-runtime', () => ({
  useChatRuntime: mocks.useChatRuntime,
}))

vi.mock('@/lib/chat/langgraph-runtime/use-moldy-langgraph-stream', () => ({
  useMoldyLangGraphStream: mocks.useMoldyLangGraphStream,
}))

vi.mock('@assistant-ui/react', () => ({
  AssistantRuntimeProvider: ({ runtime, children }: { runtime: string; children: ReactNode }) => (
    <div data-runtime={runtime} data-testid="assistant-runtime-provider">
      {children}
    </div>
  ),
  makeAssistantDataUI: (config: { name: string; render: unknown }) =>
    Object.assign(() => null, { unstable_data: config }),
}))

vi.mock('../assistant-thread', () => ({
  AssistantThread: ({
    activities,
    conversationId,
  }: {
    activities?: readonly unknown[]
    conversationId?: string
  }) => (
    <div
      data-activity-count={activities?.length ?? 0}
      data-conversation-id={conversationId ?? 'draft'}
      data-testid="assistant-thread"
    />
  ),
}))

vi.mock('@/lib/chat/tool-ui-registry', () => ({
  ALL_TOOL_UI: [],
}))

const messages: Message[] = []
const activeRun: ConversationRun | null = null

async function* emptyStream(): AsyncGenerator<SSEEvent> {
  return
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
      stream: { isLoading: false },
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
      stream: { isLoading: false },
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
      stream: { isLoading: true },
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
      stream: { isLoading: false },
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
