import { QueryClientProvider } from '@tanstack/react-query'
import { act } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch, ApiError } from '@/lib/api/client'
import { conversationGoalKeys } from '@/lib/hooks/use-conversation-goal'
import {
  createTestQueryClient,
  render,
  screen,
  userEvent,
  waitFor,
} from '../../../../../../../tests/test-utils'
import { ConversationGoalCard } from './conversation-goal-card'

vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiFetch: vi.fn(),
}))

const fetchMock = vi.mocked(apiFetch)
const cid = '389bc83a-5268-44bf-b900-4724c8b10001'
const check = {
  criterion_index: 0,
  passed: true,
  reason: 'verified',
  artifact_id: '689bc83a-5268-44bf-b900-4724c8b10002',
  version_id: '789bc83a-5268-44bf-b900-4724c8b10003',
  source_run_id: '889bc83a-5268-44bf-b900-4724c8b10004',
  version_number: 2,
  sha256: 'a'.repeat(64),
  logical_path: 'report.csv',
}
const goal = {
  id: '989bc83a-5268-44bf-b900-4724c8b10005',
  title: '확인된 분기별 보고서',
  criteria: [{ kind: 'nonempty_file' as const, logical_path: check.logical_path }],
  version: 4,
  revision: 8,
  status: 'completed' as const,
  auto_followups: 1,
  blocked_reason: null,
  last_evidence: { all_passed: true, progress: 1, checks: [check] },
}
const reference = {
  source_run_id: check.source_run_id,
  history_id: 'history_' + 'a'.repeat(24),
  checkpoint_id: 'cp-1',
  cutoff_index: 2,
}
const openHistory = '결과 생성 당시 대화 보기'
const archiveDigest = 'd'.repeat(64)
const goalPath = `/api/conversations/${cid}/goal`
const evidencePath = `/api/conversations/${cid}/history-evidence/cp-1`

function page(text: string, next_offset: number | null = null) {
  return {
    ...reference,
    format: 'jsonl',
    content_sha256: archiveDigest,
    excerpts: [{ role: 'assistant', text, truncated: false }],
    next_offset,
  }
}

function mount() {
  const client = createTestQueryClient()
  render(
    <QueryClientProvider client={client}>
      <ConversationGoalCard conversationId={cid} />
    </QueryClientProvider>,
  )
  return { client, user: userEvent.setup() }
}

function requestAt(index: number) {
  const [url, init] = fetchMock.mock.calls[index]
  expect(init?.method ?? 'GET').toBe('GET')
  return new URL(url, 'https://moldy.test')
}

beforeEach(() => fetchMock.mockReset())

describe('archived chat evidence for a verified goal artifact', () => {
  it('룩업을 목표 버전과 산출물에 묶고, 원문은 사용자가 열 때만 같은 체크포인트에서 읽는다', async () => {
    fetchMock
      .mockResolvedValueOnce({ enabled: true, goal })
      .mockResolvedValueOnce(reference)
      .mockResolvedValueOnce(page('당시 사용한 집계 기준', 10))
      .mockResolvedValueOnce(page('확인한 예외 항목'))
    const { user } = mount()
    const title = await screen.findByText(goal.title)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await user.click(title)

    const button = await screen.findByRole('button', { name: openHistory })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const lookup = requestAt(1)
    expect(lookup.pathname).toBe(`${goalPath}/evidence/${check.version_id}/history-reference`)
    expect(lookup.searchParams.get('goal_id')).toBe(goal.id)
    expect(lookup.searchParams.get('goal_version')).toBe(String(goal.version))
    await user.click(button)
    expect(await screen.findByText('당시 사용한 집계 기준')).toBeVisible()
    expect(requestAt(2).pathname).toBe(evidencePath)
    await user.click(screen.getByRole('button', { name: '더 보기' }))
    expect(await screen.findByText('확인한 예외 항목')).toBeVisible()
    const next = requestAt(3)
    expect(next.pathname).toBe(evidencePath)
    expect(next.searchParams.get('offset')).toBe('10')
    expect(next.searchParams.get('expected_sha256')).toBe(archiveDigest)
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it.each([
    ['보관 이력 없음', null],
    ['다른 실행', { ...reference, source_run_id: goal.id }],
    ['체크포인트 없음', { ...reference, checkpoint_id: '' }],
    ['권한 회수', new ApiError(403, 'forbidden', 'Forbidden')],
  ])('%s이면 원문을 조회하지 않고 산출물 링크를 유지한다', async (_label, result) => {
    fetchMock.mockResolvedValueOnce({ enabled: true, goal })
    if (result instanceof Error) fetchMock.mockRejectedValueOnce(result)
    else fetchMock.mockResolvedValueOnce(result)
    const { user, client } = mount()
    await user.click(await screen.findByText(goal.title))
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(client.isFetching()).toBe(0)
    })
    expect(screen.queryByRole('button', { name: openHistory })).toBeNull()
    expect(screen.getByRole('link', { name: /report\.csv/ })).toBeVisible()
  })

  it.each([
    { passed: false },
    { reason: 'empty_file' },
    { source_run_id: null },
    { version_id: null },
    { artifact_id: null },
    { sha256: null },
  ])('검수 영수증이 불완전하면 추가 조회를 하지 않는다 (%o)', async (missing) => {
    fetchMock.mockResolvedValueOnce({
      enabled: true,
      goal: {
        ...goal,
        last_evidence: { ...goal.last_evidence, checks: [{ ...check, ...missing }] },
      },
    })
    const { user } = mount()
    await user.click(await screen.findByText(goal.title))
    expect(screen.queryByRole('button', { name: openHistory })).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it.each(['goal version', 'producer', 'artifact digest'])(
    '%s 변경 시 이전 본문이 닫힌다',
    async (change) => {
      fetchMock
        .mockResolvedValueOnce({ enabled: true, goal })
        .mockResolvedValueOnce(reference)
        .mockResolvedValueOnce(page('이전 실행 근거'))
        .mockResolvedValueOnce(null)
      const { client, user } = mount()
      await user.click(await screen.findByText(goal.title))
      await user.click(await screen.findByRole('button', { name: openHistory }))
      expect(await screen.findByText('이전 실행 근거')).toBeVisible()
      const changed =
        change === 'goal version'
          ? { ...goal, version: goal.version + 1 }
          : {
              ...goal,
              last_evidence: {
                ...goal.last_evidence,
                checks: [
                  change === 'producer'
                    ? { ...check, source_run_id: goal.id }
                    : { ...check, sha256: 'b'.repeat(64) },
                ],
              },
            }
      act(() => {
        client.setQueryData(conversationGoalKeys.detail(cid), { enabled: true, goal: changed })
      })
      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledTimes(4)
        expect(client.isFetching()).toBe(0)
      })
      expect(screen.queryByText('이전 실행 근거')).toBeNull()
      expect(screen.queryByRole('button', { name: openHistory })).toBeNull()
    },
  )
})
