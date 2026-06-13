import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../../../../tests/test-utils'
import { PhaseTimelineRender } from '../phase-timeline-ui'

type ThreadMessageLite = {
  readonly role?: string
  readonly content?: readonly {
    readonly type?: string
    readonly toolName?: string
    readonly toolCallId?: string
  }[]
}

type AuiState = {
  readonly thread?: {
    readonly messages?: readonly ThreadMessageLite[]
  }
}

type MutableAuiState = {
  thread: {
    messages: readonly ThreadMessageLite[]
  }
}

const mocks = vi.hoisted(() => {
  const state: MutableAuiState = {
    thread: {
      messages: [],
    },
  }
  return { state }
})

vi.mock('@assistant-ui/react', () => ({
  makeAssistantToolUI: (config: unknown) => config,
  useAuiState: <T,>(selector: (state: AuiState) => T): T => selector(mocks.state),
}))

describe('PhaseTimelineRender', () => {
  beforeEach(() => {
    mocks.state.thread = {
      messages: [
        {
          role: 'assistant',
          content: [{ type: 'tool-call', toolName: 'phase_timeline', toolCallId: 'phase-old' }],
        },
        {
          role: 'assistant',
          content: [{ type: 'tool-call', toolName: 'phase_timeline', toolCallId: 'phase-latest' }],
        },
      ],
    }
  })

  it('renders the latest phase_timeline tool call from assistant-ui thread state', () => {
    render(
      <PhaseTimelineRender
        toolCallId="phase-latest"
        args={{
          todos: [
            { id: 1, name: '요구사항 정리', status: 'completed' },
            { id: 2, name: '런타임 연결', status: 'pending' },
          ],
        }}
      />,
    )

    expect(screen.getByText('요구사항 정리')).toBeInTheDocument()
    expect(screen.getByText('런타임 연결')).toBeInTheDocument()
    expect(screen.getByText('진행중')).toBeInTheDocument()
  })

  it('hides replayed older phase_timeline tool calls', () => {
    render(
      <PhaseTimelineRender
        toolCallId="phase-old"
        args={{
          todos: [{ id: 1, name: '이전 단계', status: 'completed' }],
        }}
      />,
    )

    expect(screen.queryByText('이전 단계')).not.toBeInTheDocument()
  })
})
