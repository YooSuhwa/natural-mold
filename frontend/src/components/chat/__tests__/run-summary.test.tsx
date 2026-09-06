import { describe, expect, it } from 'vitest'
import userEvent from '@testing-library/user-event'
import { render as renderWithoutProviders } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { render, screen } from '../../../../tests/test-utils'
import { RunSummaryPanel } from '../run-summary'
import type { RunSummary } from '@/lib/chat/run-summary-model'
import englishMessages from '../../../../messages/en.json'

function summary(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    runId: 'run-1',
    status: 'completed',
    elapsedMs: 2_400,
    startedAtMs: null,
    isLive: false,
    rootToolCalls: 2,
    descendantToolCalls: 3,
    rootSubagentCalls: 1,
    descendantSubagentCalls: 2,
    activities: [
      {
        key: 'first',
        kind: 'tool',
        namespace: [],
        callId: 'call-1',
        name: 'search',
        elapsedMs: 400,
      },
      {
        key: 'second',
        kind: 'subagent',
        namespace: ['researcher'],
        callId: 'call-2',
        name: 'researcher',
        elapsedMs: 900,
      },
    ],
    activityTruncated: false,
    ...overrides,
  }
}

describe('RunSummaryPanel', () => {
  it('shows a concise run summary and expands the full ordered history', async () => {
    const user = userEvent.setup()
    render(<RunSummaryPanel summary={summary()} />)

    expect(screen.getByText('2.4초')).toBeInTheDocument()
    expect(screen.getByText('도구 총 5')).toBeInTheDocument()
    expect(screen.getByText('서브 에이전트 총 3')).toBeInTheDocument()
    expect(screen.queryByText('search')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '활동 보기' }))

    expect(screen.getByText('메인 도구').nextElementSibling).toHaveTextContent('2')
    expect(screen.getByText('하위 도구').nextElementSibling).toHaveTextContent('3')
    expect(screen.getByText('메인 서브 에이전트').nextElementSibling).toHaveTextContent('1')
    expect(screen.getByText('하위 서브 에이전트').nextElementSibling).toHaveTextContent('2')

    const rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('search')
    expect(rows[1]).toHaveTextContent('researcher')
  })

  it('formats elapsed time with the active English locale', () => {
    const result = renderWithoutProviders(
      <NextIntlClientProvider locale="en" messages={englishMessages}>
        <RunSummaryPanel summary={summary({ elapsedMs: 65_000 })} />
      </NextIntlClientProvider>,
    )

    expect(result.getByText('1m 5s')).toBeInTheDocument()
  })

  it('labels missing historical metrics honestly instead of displaying zeros', async () => {
    const user = userEvent.setup()
    render(
      <RunSummaryPanel
        summary={summary({
          elapsedMs: null,
          rootToolCalls: null,
          descendantToolCalls: null,
          rootSubagentCalls: null,
          descendantSubagentCalls: null,
          activities: [],
        })}
      />,
    )

    expect(screen.getByText('시간 정보 없음')).toBeInTheDocument()
    expect(screen.getByText('도구 총 –')).toBeInTheDocument()
    expect(screen.getByText('서브 에이전트 총 –')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '활동 보기' }))
    expect(screen.getByText('메인 도구').nextElementSibling).toHaveTextContent('–')
    expect(screen.getByText('하위 도구').nextElementSibling).toHaveTextContent('–')
    expect(screen.getByText('메인 서브 에이전트').nextElementSibling).toHaveTextContent('–')
    expect(screen.getByText('하위 서브 에이전트').nextElementSibling).toHaveTextContent('–')
  })

  it('discloses when the persisted activity history was truncated', async () => {
    const user = userEvent.setup()
    render(<RunSummaryPanel summary={summary({ activityTruncated: true })} />)

    await user.click(screen.getByRole('button', { name: '활동 보기' }))

    expect(screen.getByText('이전 활동 일부가 생략되었습니다.')).toBeInTheDocument()
  })
})
