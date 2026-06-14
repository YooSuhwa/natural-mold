import { AIMessage } from '@langchain/core/messages'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../../../tests/test-utils'
import { SubagentCard } from '../subagent-card'

const mocks = vi.hoisted(() => ({
  useMessages: vi.fn(),
  useToolCalls: vi.fn(),
  useSubagentInlinePolicy: vi.fn(),
  useSubagentSnapshot: vi.fn(),
  useSubagentStream: vi.fn(),
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
  namespace: ['tools:tc-task-1'],
  parentId: null,
  depth: 0,
  status: 'complete',
  taskInput: '시장 자료를 조사해줘',
  output: '조사 완료',
  error: undefined,
  startedAt: new Date('2026-06-13T00:00:00Z'),
  completedAt: new Date('2026-06-13T00:01:00Z'),
}

function renderCard() {
  return render(
    <SubagentCard
      fallback={{ agentName: 'fallback', input: 'fallback input', status: 'loading' }}
      toolCallId="tc-task-1"
    />,
  )
}

describe('SubagentCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.useSubagentSnapshot.mockReturnValue(researcherSnapshot)
    mocks.useSubagentStream.mockReturnValue(streamToken)
    mocks.useMessages.mockReturnValue([new AIMessage('세부 메시지')])
    mocks.useToolCalls.mockReturnValue([])
    mocks.useSubagentInlinePolicy.mockReturnValue({
      defaultExpanded: false,
      canRenderInlineDetails: true,
      overflowedLiveDetails: false,
    })
  })

  it('keeps scoped root-transcript details unmounted while a completed card is collapsed', () => {
    renderCard()

    expect(screen.getByText('researcher')).toBeInTheDocument()
    expect(screen.queryByText('세부 메시지')).not.toBeInTheDocument()
    expect(mocks.useMessages).not.toHaveBeenCalled()
    expect(mocks.useToolCalls).not.toHaveBeenCalled()
  })

  it('mounts scoped detail selectors for default-expanded live cards', () => {
    mocks.useSubagentSnapshot.mockReturnValue({
      ...researcherSnapshot,
      status: 'running',
      completedAt: null,
      output: undefined,
    })
    mocks.useSubagentInlinePolicy.mockReturnValue({
      defaultExpanded: true,
      canRenderInlineDetails: true,
      overflowedLiveDetails: false,
    })

    renderCard()

    expect(mocks.useMessages).toHaveBeenCalledWith(streamToken, {
      ...researcherSnapshot,
      status: 'running',
      completedAt: null,
      output: undefined,
    })
    expect(screen.getByText('세부 메시지')).toBeInTheDocument()
  })

  it('redacts private reasoning parts from expanded scoped messages', () => {
    mocks.useSubagentSnapshot.mockReturnValue({
      ...researcherSnapshot,
      status: 'running',
      completedAt: null,
      output: undefined,
    })
    mocks.useSubagentInlinePolicy.mockReturnValue({
      defaultExpanded: true,
      canRenderInlineDetails: true,
      overflowedLiveDetails: false,
    })
    mocks.useMessages.mockReturnValue([
      new AIMessage({
        content: [
          { type: 'text', text: '보이는 세부 메시지' },
          { type: 'reasoning', text: 'hidden chain' },
          { type: 'thinking', text: 'private thought' },
          { type: 'text', reasoning: 'raw private reason', text: '안전한 후속 메시지' },
        ],
      }),
    ])

    renderCard()

    expect(screen.getByText('보이는 세부 메시지안전한 후속 메시지')).toBeInTheDocument()
    expect(screen.queryByText(/hidden chain/)).not.toBeInTheDocument()
    expect(screen.queryByText(/private thought/)).not.toBeInTheDocument()
    expect(screen.queryByText(/raw private reason/)).not.toBeInTheDocument()
  })

  it('does not mount scoped detail selectors for overflow live cards', () => {
    mocks.useSubagentSnapshot.mockReturnValue({
      ...researcherSnapshot,
      status: 'running',
      completedAt: null,
      output: undefined,
    })
    mocks.useSubagentInlinePolicy.mockReturnValue({
      defaultExpanded: false,
      canRenderInlineDetails: false,
      overflowedLiveDetails: true,
    })

    renderCard()

    expect(screen.getByText('researcher')).toBeInTheDocument()
    expect(screen.queryByText('세부 메시지')).not.toBeInTheDocument()
    expect(mocks.useMessages).not.toHaveBeenCalled()
    expect(mocks.useToolCalls).not.toHaveBeenCalled()
  })

  it('keeps rail action buttons outside clickable subagent pill buttons', () => {
    mocks.useSubagentSnapshot.mockReturnValue(null)
    mocks.useSubagentStream.mockReturnValue(null)
    mocks.useSubagentInlinePolicy.mockReturnValue({
      defaultExpanded: false,
      canRenderInlineDetails: false,
      overflowedLiveDetails: false,
    })

    renderCard()

    for (const button of screen.getAllByRole('button')) {
      expect(button.querySelector('button')).toBeNull()
    }
  })
})
