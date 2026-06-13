import { describe, expect, it } from 'vitest'
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
  it('renders nothing when there is no semantic activity', () => {
    const { container } = render(<RunActivityStrip activities={[]} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('renders compact translated activity rows and limits the visible set', () => {
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

    expect(screen.getByText('계획을 세우는 중')).toBeInTheDocument()
    expect(screen.getByText('web_search 실행 중')).toBeInTheDocument()
    expect(screen.getByText('researcher 작업 중')).toBeInTheDocument()
    expect(screen.queryByText('파일을 준비하는 중')).not.toBeInTheDocument()
  })

  it('marks error activity rows with error status', () => {
    render(
      <RunActivityStrip
        activities={[activity({ id: 'error', kind: 'error', status: 'error', title: 'Error' })]}
      />,
    )

    expect(screen.getByText('문제가 발생했습니다').closest('[data-status="error"]')).not.toBeNull()
  })
})
