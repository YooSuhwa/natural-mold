import { AIMessage } from '@langchain/core/messages'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, userEvent } from '../../../../../tests/test-utils'
import { SubAgentToolCard } from '../sub-agent-ui'

const mocks = vi.hoisted(() => ({
  makeAssistantToolUI: vi.fn((config: { render: unknown; toolName: string }) => config),
  useMessage: vi.fn(),
  useMessages: vi.fn(),
  useToolCalls: vi.fn(),
  useSubagentInlinePolicy: vi.fn(),
  useSubagentSnapshot: vi.fn(),
  useSubagentStream: vi.fn(),
}))

vi.mock('@assistant-ui/react', () => ({
  makeAssistantToolUI: mocks.makeAssistantToolUI,
  useMessage: mocks.useMessage,
}))

vi.mock('@langchain/react', () => ({
  useMessages: mocks.useMessages,
  useToolCalls: mocks.useToolCalls,
}))

vi.mock('@/components/chat/conversation-context', () => ({
  useChatConversationId: () => 'conversation-1',
}))

vi.mock('@/lib/chat/langgraph-runtime/subagent-runtime', () => ({
  useSubagentInlinePolicy: mocks.useSubagentInlinePolicy,
  useSubagentSnapshot: mocks.useSubagentSnapshot,
  useSubagentStream: mocks.useSubagentStream,
}))

const streamToken = { stream: 'shared' }

const researcherSnapshot = {
  id: 'tc-task-1',
  name: 'researcher',
  namespace: ['tools:exec-1'],
  parentId: null,
  depth: 0,
  status: 'running',
  taskInput: '시장 자료를 조사해줘',
  output: undefined,
  error: undefined,
  startedAt: new Date('2026-06-13T00:00:00Z'),
  completedAt: null,
}

function renderCard(statusType = 'running') {
  return render(
    <SubAgentToolCard
      args={{ subagent_type: 'fallback-researcher', description: 'fallback input' }}
      statusType={statusType}
      toolCallId="tc-task-1"
    />,
  )
}

describe('SubAgentToolCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.useMessage.mockImplementation(
      (selector: (message: { content: readonly unknown[] }) => unknown) =>
        selector({
          content: [
            { type: 'tool-call', toolName: 'task', toolCallId: 'tc-task-1' },
            { type: 'tool-call', toolName: 'web_search', toolCallId: 'tc-search-1' },
          ],
        }),
    )
    mocks.useSubagentSnapshot.mockReturnValue(researcherSnapshot)
    mocks.useSubagentStream.mockReturnValue(streamToken)
    mocks.useSubagentInlinePolicy.mockReturnValue({
      defaultExpanded: false,
      canRenderInlineDetails: true,
      overflowedLiveDetails: false,
    })
    mocks.useMessages.mockReturnValue([])
    mocks.useToolCalls.mockReturnValue([])
  })

  it('renders the discovered LangGraph subagent snapshot while collapsed', () => {
    renderCard()

    expect(screen.getByText('researcher')).toBeInTheDocument()
    expect(screen.getByText('시장 자료를 조사해줘')).toBeInTheDocument()
    expect(screen.getByText('tools:exec-1')).toBeInTheDocument()
    expect(mocks.useMessages).not.toHaveBeenCalled()
    expect(mocks.useToolCalls).not.toHaveBeenCalled()
  })

  it('subscribes to scoped messages and tools only after expansion', async () => {
    mocks.useMessages.mockReturnValue([new AIMessage('조사를 시작했어요')])
    mocks.useToolCalls.mockReturnValue([
      {
        name: 'web_search',
        callId: 'tool-1',
        id: 'tool-1',
        namespace: ['tools:exec-1'],
        input: { query: 'market' },
        args: { query: 'market' },
        output: null,
        status: 'running',
        error: undefined,
      },
    ])

    renderCard()

    expect(mocks.useMessages).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: /researcher/ }))

    expect(mocks.useMessages).toHaveBeenCalledWith(streamToken, researcherSnapshot)
    expect(mocks.useToolCalls).toHaveBeenCalledWith(streamToken, researcherSnapshot)
    expect(screen.getByText('조사를 시작했어요')).toBeInTheDocument()
    expect(screen.getByText('web_search')).toBeInTheDocument()
  })

  it('falls back to the legacy task tool arguments before discovery arrives', () => {
    mocks.useSubagentSnapshot.mockReturnValue(null)

    renderCard()

    expect(screen.getByText('fallback-researcher')).toBeInTheDocument()
    expect(screen.getByText('fallback input')).toBeInTheDocument()
    expect(screen.queryByText('tools:exec-1')).not.toBeInTheDocument()
  })

  it('keeps subagent errors inside the expanded card', async () => {
    mocks.useSubagentSnapshot.mockReturnValue({
      ...researcherSnapshot,
      status: 'error',
      error: '검색 도구 실패',
      completedAt: new Date('2026-06-13T00:01:00Z'),
    })

    renderCard('complete')

    await userEvent.click(screen.getByRole('button', { name: /researcher/ }))

    expect(screen.getByText('검색 도구 실패')).toBeInTheDocument()
    expect(screen.queryByText('문제가 발생했습니다')).not.toBeInTheDocument()
  })
})
