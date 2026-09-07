import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { render, screen } from '../../../../tests/test-utils'
import { RunActivityStrip } from '../run-activity-strip'
import type { RunActivity } from '@/lib/chat/langgraph-runtime/activity-model'

function activity(overrides: Partial<RunActivity>): RunActivity {
  return {
    id: overrides.id ?? 'activity-1',
    runId: overrides.runId ?? 'run-1',
    kind: overrides.kind ?? 'responding',
    status: overrides.status ?? 'running',
    title: overrides.title ?? 'Responding',
    namespace: overrides.namespace ?? [],
    ...overrides,
  }
}

describe('RunActivityStrip', () => {
  afterEach(() => vi.useRealTimers())

  it('renders nothing when there is no semantic activity', () => {
    const { container } = render(<RunActivityStrip activities={[]} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('renders a concise summary and exposes the complete current-run history', async () => {
    render(
      <RunActivityStrip
        activities={[
          activity({ id: 'planning', kind: 'planning', title: 'Planning' }),
          activity({ id: 'tool', kind: 'tool', title: 'web_search' }),
          activity({
            id: 'subagent',
            kind: 'subagent',
            title: 'researcher',
            namespace: ['supervisor', 'researcher'],
          }),
          activity({ id: 'artifact', kind: 'artifact', title: 'Artifact' }),
        ]}
      />,
    )

    expect(screen.getByText('도구 총 1')).toBeInTheDocument()
    expect(screen.getByText('서브 에이전트 총 1')).toBeInTheDocument()

    await screen.getByRole('button', { name: '활동 보기' }).click()

    expect(screen.getAllByRole('listitem')).toHaveLength(4)
    expect(screen.getByText('web_search')).toBeInTheDocument()
    expect(screen.getByText('researcher')).toBeInTheDocument()
    expect(screen.getByText('Artifact')).toBeInTheDocument()
  })

  it('keeps a terminal activity visible without inventing elapsed time', async () => {
    render(
      <RunActivityStrip
        activities={[activity({ id: 'error', kind: 'error', status: 'error', title: 'Error' })]}
      />,
    )

    expect(screen.getByText('시간 정보 없음')).toBeInTheDocument()
    await screen.getByRole('button', { name: '활동 보기' }).click()
    expect(screen.getByText('Error')).toBeInTheDocument()
  })

  it('advances the bounded live timer and freezes it at the terminal event', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-06T00:00:02.000Z'))
    const running = activity({
      id: 'timed-tool',
      kind: 'tool',
      title: 'search',
      startedAt: '2026-09-06T00:00:00.000Z',
    })
    const { rerender } = render(<RunActivityStrip activities={[running]} />)

    expect(screen.getByText('2초')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(1_000)
    })
    expect(screen.getByText('3초')).toBeInTheDocument()

    rerender(
      <RunActivityStrip
        activities={[
          {
            ...running,
            status: 'complete',
            endedAt: '2026-09-06T00:00:03.500Z',
          },
        ]}
      />,
    )
    expect(screen.getByText('3.5초')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(5_000)
    })
    expect(screen.getByText('3.5초')).toBeInTheDocument()
  })
})
