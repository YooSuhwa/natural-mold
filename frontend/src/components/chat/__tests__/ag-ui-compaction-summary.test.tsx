import { act } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AgUiActivityProvider } from '@/lib/ag-ui/activity-context'
import { apiFetch } from '@/lib/api/client'
import { dispatchMoldyBranchSwitched } from '@/lib/chat/langgraph-runtime/branch-switch-events'
import { render, screen, userEvent } from '../../../../tests/test-utils'
import { CompactionSummary } from '../compaction-summary'

vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiFetch: vi.fn(),
}))

const target = {
  conversationId: '9fee1cc1-ce15-43da-a42e-c313208a1e66',
  messageId: 'wire-answer',
  activityId: 'stored-summary',
  historyId: 'history_0123456789abcdef01234567',
  cutoffIndex: 7,
}
const base = `/api/conversations/${target.conversationId}`
const checkpoint = 'f1a66d33-c13d-4fd5-a6a3-2001f06562fa'
const fetchMock = vi.mocked(apiFetch)

const physicalRunId = '3e944392-1e08-4734-beff-b4dbcab02c53'
interface SnapshotOptions {
  activityId?: string
  cutoff?: number
  ownerCheckpoint?: string | null
  activityCheckpoint?: string
  markerRun?: string
}

function snapshot({
  activityId = target.activityId,
  cutoff = target.cutoffIndex,
  ownerCheckpoint = checkpoint,
  activityCheckpoint,
  markerRun = physicalRunId,
}: SnapshotOptions = {}) {
  const run = { runId: 'public-run', physicalRunId }
  return {
    threadId: target.conversationId,
    protocolVersion: '1.0',
    projectionVersion: '1',
    checkpointId: 'later-branch-head',
    activeTipMessageId: target.messageId,
    messages: [
      { id: target.messageId, role: 'assistant', content: '요약 후 응답' },
      {
        id: activityId,
        role: 'activity',
        activityType: 'moldy.compaction',
        content: { state: 'done', history_id: target.historyId, cutoff_index: cutoff },
      },
    ],
    messageLinks: [
      {
        ...run,
        messageId: target.messageId,
        uiMessageId: 'display-row',
        checkpointId: ownerCheckpoint,
      },
      {
        ...run,
        physicalRunId: markerRun,
        messageId: activityId,
        parentMessageId: target.messageId,
        checkpointId: activityCheckpoint ?? null,
      },
    ],
    subagents: [],
    interrupts: [],
  }
}

function summary() {
  return (
    <AgUiActivityProvider conversationId={target.conversationId} activities={[]}>
      <CompactionSummary evidence={target} />
    </AgUiActivityProvider>
  )
}

function requests() {
  return fetchMock.mock.calls.map(([url, options]) => [
    options?.method ?? 'GET',
    new URL(url, 'https://moldy.test').pathname,
  ])
}

beforeEach(() => {
  fetchMock.mockReset()
})

describe('AG-UI 요약 원문', () => {
  it('처음 열면 일반 채팅 스냅샷에서 요약 메시지의 체크포인트를 확인해 원문을 읽는다', async () => {
    fetchMock.mockResolvedValueOnce(snapshot()).mockResolvedValueOnce({
      checkpoint_id: checkpoint,
      history_id: target.historyId,
      cutoff_index: target.cutoffIndex,
      content_sha256: 'a'.repeat(64),
      excerpts: [{ role: 'assistant', text: '선택한 요약의 원문' }],
      next_offset: null,
    })
    const user = userEvent.setup()
    render(summary())
    expect(fetchMock).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '요약 전 대화 보기' }))

    expect(await screen.findByText('선택한 요약의 원문')).toBeVisible()
    expect(requests()).toEqual([
      ['GET', base + '/ag-ui/snapshot'],
      ['GET', base + '/history-evidence/' + checkpoint],
    ])
  })

  it('체크포인트를 확인하는 동안 분기가 바뀌면 이전 조회를 취소한다', async () => {
    const delayed = Promise.withResolvers<ReturnType<typeof snapshot>>()
    fetchMock.mockReturnValueOnce(delayed.promise)
    const user = userEvent.setup()
    render(summary())
    await user.click(screen.getByRole('button', { name: '요약 전 대화 보기' }))
    const signal = fetchMock.mock.calls[0]?.[1]?.signal

    act(() =>
      dispatchMoldyBranchSwitched({
        conversationId: target.conversationId,
        checkpointId: 'branch-b',
      }),
    )
    expect(signal?.aborted).toBe(true)
    await act(async () => {
      delayed.resolve(snapshot())
      await delayed.promise
    })

    expect(screen.getByRole('button', { name: '요약 전 대화 보기' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(requests()).toEqual([['GET', base + '/ag-ui/snapshot']])
  })

  it.each([
    ['원문 범위가 달라짐', { cutoff: target.cutoffIndex + 1 }],
    ['같은 메시지 ID에 다른 요약이 연결됨', { activityId: 'another-summary' }],
    ['마커와 응답의 실행이 다름', { markerRun: '3aacd22e-3488-43de-b345-e07f65bdaf2c' }],
    ['마커에만 체크포인트가 있음', { ownerCheckpoint: null, activityCheckpoint: checkpoint }],
  ] satisfies [string, SnapshotOptions][])(
    '응답 메시지와 원문의 연결을 확인할 수 없으면 근거를 요청하지 않는다 (%s)',
    async (_reason, options) => {
      fetchMock.mockResolvedValueOnce(snapshot(options))
      const user = userEvent.setup()
      render(summary())
      await user.click(screen.getByRole('button', { name: '요약 전 대화 보기' }))

      expect(await screen.findByText('이 요약에 연결된 대화를 확인할 수 없어요.')).toBeVisible()
      expect(requests()).toEqual([['GET', base + '/ag-ui/snapshot']])
    },
  )
})
