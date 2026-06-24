import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../../../tests/test-utils'
import { StreamingMessageLoadingIndicator } from '../assistant-message-loading'
import type { RunActivity } from '@/lib/chat/langgraph-runtime/activity-model'

const mocks = vi.hoisted(() => ({
  state: {
    thread: { isRunning: true },
    message: {
      metadata: { custom: { isStreamingMessage: true as boolean | undefined } },
      status: undefined as { readonly type?: string } | undefined,
      parts: [] as readonly unknown[],
    },
  },
  useSubagentProgressSummary: vi.fn(),
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

vi.mock('@/lib/chat/langgraph-runtime/subagent-runtime', () => ({
  useSubagentProgressSummary: mocks.useSubagentProgressSummary,
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
    vi.clearAllMocks()
    mocks.state.thread.isRunning = true
    mocks.state.message.metadata = { custom: { isStreamingMessage: true } }
    mocks.state.message.status = undefined
    mocks.state.message.parts = []
    mocks.useSubagentProgressSummary.mockReturnValue({
      total: 0,
      running: 0,
      completed: 0,
      failed: 0,
    })
  })

  it('shows witty loading when no semantic activity exists', () => {
    render(<StreamingMessageLoadingIndicator activities={[]} />)

    expect(screen.getByTestId('witty-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('run-activity-strip')).not.toBeInTheDocument()
  })

  it('shows witty loading when assistant-ui marks the message running', () => {
    mocks.state.message.metadata = { custom: { isStreamingMessage: undefined } }
    mocks.state.message.status = { type: 'running' }

    render(<StreamingMessageLoadingIndicator activities={[]} />)

    expect(screen.getByTestId('witty-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('run-activity-strip')).not.toBeInTheDocument()
  })

  it('hides witty loading when semantic activity exists', () => {
    render(<StreamingMessageLoadingIndicator activities={[activity()]} />)

    expect(screen.getByTestId('run-activity-strip')).toBeInTheDocument()
    expect(screen.queryByTestId('witty-loading')).not.toBeInTheDocument()
  })

  it('keeps witty loading when only interrupt activities exist', () => {
    render(
      <StreamingMessageLoadingIndicator
        activities={[
          activity({
            id: 'run-1:interrupt:one',
            kind: 'interrupt',
            status: 'requires_action',
            title: 'Needs approval',
          }),
          activity({
            id: 'run-1:interrupt:two',
            kind: 'interrupt',
            status: 'requires_action',
            title: 'Needs approval',
          }),
        ]}
      />,
    )

    expect(screen.getByTestId('witty-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('run-activity-strip')).not.toBeInTheDocument()
  })

  it('keeps witty loading when only generic responding and terminal rows exist while streaming', () => {
    render(
      <StreamingMessageLoadingIndicator
        activities={[
          activity({
            id: 'run-1:responding:root',
            kind: 'responding',
            status: 'running',
            title: 'Responding',
          }),
          activity({
            id: 'run-1:responding:old',
            kind: 'responding',
            status: 'complete',
            title: 'Responding',
          }),
          activity({
            id: 'run-1:done:run',
            kind: 'done',
            status: 'complete',
            title: 'Done',
          }),
        ]}
      />,
    )

    expect(screen.queryByTestId('run-activity-strip')).not.toBeInTheDocument()
    expect(screen.getByTestId('witty-loading')).toBeInTheDocument()
    expect(screen.queryByText('응답을 작성하는 중')).not.toBeInTheDocument()
    expect(screen.queryByText('완료됨')).not.toBeInTheDocument()
  })

  it('keeps witty loading once assistant text is visible while streaming', () => {
    mocks.state.message.parts = [{ type: 'text', text: 'Partial assistant response' }]

    render(
      <StreamingMessageLoadingIndicator
        activities={[
          activity({
            id: 'run-1:responding:root',
            kind: 'responding',
            status: 'running',
            title: 'Responding',
          }),
        ]}
      />,
    )

    expect(screen.queryByTestId('run-activity-strip')).not.toBeInTheDocument()
    expect(screen.getByTestId('witty-loading')).toBeInTheDocument()
  })

  it('keeps non-text progress visible while assistant text is streaming', () => {
    mocks.state.message.parts = [{ type: 'text', text: 'Partial assistant response' }]

    render(
      <StreamingMessageLoadingIndicator
        activities={[
          activity({
            id: 'run-1:tool:search',
            kind: 'tool',
            status: 'running',
            title: 'web_search',
          }),
        ]}
      />,
    )

    expect(screen.getByTestId('run-activity-strip')).toBeInTheDocument()
    expect(screen.getByText('web_search 실행 중')).toBeInTheDocument()
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

  it('M6 — treats an explicit isStreamingMessage:false as not streaming even with a stale running status', () => {
    // sticky/converted 재사용으로 완료된 메시지에 stale running이 남은 케이스.
    // metadata가 streaming=false라고 명시하면 running status보다 우선해야 한다.
    mocks.state.message.metadata = { custom: { isStreamingMessage: false } }
    mocks.state.message.status = { type: 'running' }

    const { container } = render(<StreamingMessageLoadingIndicator activities={[activity()]} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('scopes subagent progress to inline subagents in the current assistant turn', () => {
    mocks.useSubagentProgressSummary.mockImplementation((toolCallIds: readonly string[]) => {
      if (toolCallIds.includes('tc-current')) {
        return { total: 1, running: 0, completed: 1, failed: 0 }
      }
      return { total: 2, running: 0, completed: 2, failed: 0 }
    })

    render(
      <StreamingMessageLoadingIndicator
        activities={[
          activity({
            id: 'run-1:subagent:tc-current',
            kind: 'subagent',
            toolCallId: 'tc-current',
            status: 'complete',
            title: 'Researcher',
          }),
          activity({
            id: 'run-1:background_subagent:bg-1',
            kind: 'background_subagent',
            status: 'running',
            title: 'Background writer',
          }),
        ]}
      />,
    )

    expect(mocks.useSubagentProgressSummary).toHaveBeenCalledWith(['tc-current'])
    expect(screen.getByText('서브 에이전트 1/1 완료')).toBeInTheDocument()
    expect(screen.queryByText('서브 에이전트 2/2 완료')).not.toBeInTheDocument()
  })

  it('renders background subagent tasks as activity rows distinct from inline progress cards', () => {
    render(
      <StreamingMessageLoadingIndicator
        activities={[
          activity({
            id: 'run-1:background_subagent:bg-1',
            kind: 'background_subagent',
            status: 'running',
            title: 'Background writer',
          }),
        ]}
      />,
    )

    expect(screen.getByTestId('run-activity-strip')).toBeInTheDocument()
    expect(screen.getByText('Background writer 작업 중')).toBeInTheDocument()
    expect(screen.getByText('Background writer 작업 중').closest('[data-kind]')).toHaveAttribute(
      'data-kind',
      'background_subagent',
    )
    expect(
      screen.queryByRole('progressbar', { name: '서브 에이전트 진행률' }),
    ).not.toBeInTheDocument()
  })
})
