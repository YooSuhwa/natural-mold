import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor, within } from '../../../../tests/test-utils'
import { SubagentPanelContent } from '@/components/chat/right-rail/subagent-panel-content'
import { AgUiTestChat, conversationId } from '../../../../tests/ag-ui/chat-ui-harness'
import {
  cardEvents,
  createFixtureAgent,
  parallelEvents,
  savedCardSnapshot,
} from '../../../../tests/ag-ui/chat-ui-fixtures'

afterEach(() => vi.restoreAllMocks())

describe('일반 채팅의 AG-UI 화면', () => {
  it('긴 대화 요약을 한 번 표시하고 표를 렌더링한다', async () => {
    const { agent, fetch, session } = createFixtureAgent(cardEvents)
    render(<AgUiTestChat agent={agent} session={session} />)
    await userEvent.click(screen.getByRole('button', { name: 'Send fixture' }))
    const table = await screen.findByTestId('data-ui-data-table')
    expect(within(table).getByText('Receipts')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByTestId('compaction-summary')).toHaveLength(1))
    expect(screen.queryByText('history_0123456789abcdef01234567')).not.toBeInTheDocument()
    expect(screen.queryByText('hidden-unknown-data')).not.toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('저장된 대화를 조회하면 표와 요약을 복원하고 새 실행을 시작하지 않는다', async () => {
    const { agent, fetch, session } = createFixtureAgent(cardEvents, savedCardSnapshot())
    render(<AgUiTestChat agent={agent} session={session} restore />)
    const table = await screen.findByTestId('data-ui-data-table')
    expect(within(table).getByText('Receipts')).toBeInTheDocument()
    expect(screen.getAllByTestId('data-ui-data-table')).toHaveLength(1)
    expect(screen.getAllByTestId('compaction-summary')).toHaveLength(1)
    expect(fetch.mock.calls.map(([path, init]) => [path, init?.method])).toEqual([
      [`/api/conversations/${conversationId}/ag-ui/snapshot`, 'GET'],
    ])
  })

  it('동일 역할 자식이 역순으로 종료되어도 각 패널에 해당 작업의 기록만 표시한다', async () => {
    const { agent, fetch, session } = createFixtureAgent(parallelEvents)
    const panels = ['A', 'B'].map((child) => (
      <section key={child} aria-label={'Panel ' + child}>
        <SubagentPanelContent
          payload={{
            conversationId,
            toolCallId: 'task-' + child,
            subagentRunId: 'child-' + child,
            agentName: 'researcher',
          }}
        />
      </section>
    ))
    render(
      <AgUiTestChat agent={agent} session={session}>
        {panels}
      </AgUiTestChat>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Send fixture' }))
    for (const [child, sibling] of [
      ['A', 'B'],
      ['B', 'A'],
    ]) {
      const panel = within(screen.getByRole('region', { name: 'Panel ' + child }))
      await waitFor(() =>
        expect(panel.getAllByText('Evidence from ' + child).length).toBeGreaterThan(0),
      )
      expect(panel.queryByText('Evidence from ' + sibling)).not.toBeInTheDocument()
    }
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
