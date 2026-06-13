import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../../../tests/test-utils'
import { StreamingMessageLoadingIndicator } from '../assistant-message-loading'
import type { RunActivity } from '@/lib/chat/langgraph-runtime/activity-model'

const mocks = vi.hoisted(() => ({
  state: {
    thread: { isRunning: true },
    message: { metadata: { custom: { isStreamingMessage: true } } },
  },
}))

vi.mock('@assistant-ui/react', () => ({
  AuiIf: ({
    children,
    condition,
  }: {
    children: ReactNode
    condition: (state: typeof mocks.state) => boolean
  }) => (condition(mocks.state) ? <>{children}</> : null),
  useAuiState: (selector: (state: typeof mocks.state) => unknown) => selector(mocks.state),
}))

vi.mock('@/components/chat/witty-loading', () => ({
  WittyLoadingMessage: () => <div data-testid="witty-loading">witty</div>,
}))

function activity(overrides: Partial<RunActivity> = {}): RunActivity {
  return {
    id: overrides.id ?? 'activity-1',
    runId: overrides.runId ?? 'run-1',
    kind: overrides.kind ?? 'tool',
    status: overrides.status ?? 'running',
    title: overrides.title ?? 'web_search',
    namespace: overrides.namespace ?? [],
    ...overrides,
  }
}

describe('StreamingMessageLoadingIndicator', () => {
  beforeEach(() => {
    mocks.state.thread.isRunning = true
    mocks.state.message.metadata = { custom: { isStreamingMessage: true } }
  })

  it('shows witty loading when no semantic activity exists', () => {
    render(<StreamingMessageLoadingIndicator activities={[]} />)

    expect(screen.getByTestId('witty-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('run-activity-strip')).not.toBeInTheDocument()
  })

  it('hides witty loading when semantic activity exists', () => {
    render(<StreamingMessageLoadingIndicator activities={[activity()]} />)

    expect(screen.getByTestId('run-activity-strip')).toBeInTheDocument()
    expect(screen.queryByTestId('witty-loading')).not.toBeInTheDocument()
  })

  it('hides witty loading when DeepAgents state exists', () => {
    render(
      <StreamingMessageLoadingIndicator
        activities={[]}
        deepAgentsState={{
          todos: [{ id: 'todo-1', content: 'Plan work', status: 'in_progress' }],
          files: [],
        }}
      />,
    )

    expect(screen.getByText('작업 목록')).toBeInTheDocument()
    expect(screen.queryByTestId('witty-loading')).not.toBeInTheDocument()
  })

  it('renders nothing outside the active streaming message', () => {
    mocks.state.message.metadata = { custom: { isStreamingMessage: false } }

    const { container } = render(<StreamingMessageLoadingIndicator activities={[activity()]} />)

    expect(container).toBeEmptyDOMElement()
  })
})
