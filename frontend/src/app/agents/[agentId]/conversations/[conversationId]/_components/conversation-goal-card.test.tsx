import { act } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createTestQueryClient,
  render,
  screen,
  userEvent,
  waitFor,
} from '../../../../../../../tests/test-utils'
import { conversationGoalKeys } from '@/lib/hooks/use-conversation-goal'
import { ConversationGoalCard } from './conversation-goal-card'

const { getConversationGoal, putConversationGoal, changeConversationGoal } = vi.hoisted(() => ({
  getConversationGoal: vi.fn(),
  putConversationGoal: vi.fn(),
  changeConversationGoal: vi.fn(),
}))

vi.mock('@/lib/api/conversation-goals', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  getConversationGoal,
  putConversationGoal,
  changeConversationGoal,
}))

const conversationId = 'conversation-a'
const goal = {
  id: 'goal-a',
  title: '분기별 활동 보고서',
  criteria: [
    { kind: 'nonempty_file' as const, logical_path: 'report.csv' },
    { kind: 'nonempty_file' as const, logical_path: 'appendix.csv' },
  ],
  version: 2,
  revision: 7,
  status: 'active' as const,
  auto_followups: 1,
  blocked_reason: null,
  last_evidence: {
    all_passed: false,
    progress: 1,
    checks: [
      {
        criterion_index: 0,
        passed: true,
        reason: 'verified',
        artifact_id: 'report-artifact',
        version_id: 'frozen-version',
        version_number: 2,
        sha256: 'a'.repeat(64),
        logical_path: 'report.csv',
      },
      {
        criterion_index: 1,
        passed: false,
        reason: 'empty_file',
        artifact_id: 'appendix-artifact',
        version_id: 'empty-version',
        version_number: 5,
        sha256: 'b'.repeat(64),
        logical_path: 'appendix.csv',
      },
    ],
  },
}

describe('ConversationGoalCard', () => {
  beforeEach(() => vi.resetAllMocks())

  it('has no create action when goals are disabled and there is no history', async () => {
    getConversationGoal.mockResolvedValue({ enabled: false, goal: null })
    const queryClient = createTestQueryClient()
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <ConversationGoalCard conversationId={conversationId} />
      </QueryClientProvider>,
    )
    await waitFor(() =>
      expect(queryClient.getQueryCache().getAll()[0]?.state.status).toBe('success'),
    )
    expect(getConversationGoal).toHaveBeenCalledWith(conversationId)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows how to continue when a question or account connection needs input', async () => {
    getConversationGoal.mockResolvedValue({
      enabled: true,
      goal: { ...goal, status: 'blocked', blocked_reason: 'input_required' },
    })
    const user = userEvent.setup()
    render(<ConversationGoalCard conversationId={conversationId} />)
    await user.click(await screen.findByText(goal.title))
    expect(screen.getByText('채팅에서 질문에 답하거나 요청된 계정을 연결하세요.')).toBeVisible()
  })

  it('keeps historical evidence readable with the checked version when goals are disabled', async () => {
    getConversationGoal.mockResolvedValue({ enabled: false, goal })
    const user = userEvent.setup()
    render(<ConversationGoalCard conversationId={conversationId} />)
    await user.click(await screen.findByText(goal.title))
    const links = screen.getAllByRole('link')
    expect(links).toHaveLength(1)
    expect(new URL((links[0] as HTMLAnchorElement).href).pathname).toBe(
      '/api/conversations/conversation-a/goal/artifacts/report-artifact/versions/frozen-version/download',
    )
    for (const name of ['목표 수정', '일시정지', '목표 취소', '목표 만들기']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
  })

  it('pauses the observed state revision, independent of the goal content version', async () => {
    getConversationGoal.mockResolvedValue({ enabled: true, goal })
    const updated = { enabled: true, goal: { ...goal, status: 'paused' as const, revision: 8 } }
    changeConversationGoal.mockResolvedValue(updated)
    const user = userEvent.setup()
    render(<ConversationGoalCard conversationId={conversationId} />)
    await user.click(await screen.findByText(goal.title))
    getConversationGoal.mockResolvedValue(updated)
    await user.click(screen.getByRole('button', { name: '일시정지' }))
    await waitFor(() =>
      expect(changeConversationGoal).toHaveBeenCalledWith(conversationId, {
        action: 'pause',
        expected_revision: 7,
      }),
    )
    expect(await screen.findByRole('button', { name: '재개' })).toBeVisible()
  })

  it('preserves the editor revision when a newer goal arrives in the background', async () => {
    getConversationGoal.mockResolvedValue({ enabled: true, goal })
    const queryClient = createTestQueryClient()
    const user = userEvent.setup()
    render(
      <QueryClientProvider client={queryClient}>
        <ConversationGoalCard conversationId={conversationId} />
      </QueryClientProvider>,
    )
    await user.click(await screen.findByText(goal.title))
    await user.click(screen.getByRole('button', { name: '목표 수정' }))
    const titleInput = await screen.findByRole('textbox', { name: '목표 이름' })
    await user.clear(titleInput)
    await user.type(titleInput, '검토 중인 보고서')
    const newerGoal = { enabled: true, goal: { ...goal, revision: 8, title: '다른 탭의 수정' } }
    getConversationGoal.mockResolvedValue(newerGoal)
    act(() => {
      queryClient.setQueryData(conversationGoalKeys.detail(conversationId), newerGoal)
    })
    await waitFor(() => expect(screen.getByRole('button', { name: '저장' })).toBeDisabled())
    expect(titleInput).toHaveValue('검토 중인 보고서')
    expect(screen.getByRole('alert')).toBeVisible()
    expect(putConversationGoal).not.toHaveBeenCalled()
  })

  it('creates file and manual criteria at revision zero and displays the resulting goal', async () => {
    getConversationGoal.mockResolvedValue({ enabled: true, goal: null })
    const user = userEvent.setup()
    const title = '근거 자료 수집'
    const criteria = [
      { kind: 'nonempty_file' as const, logical_path: 'outputs/report.csv' },
      { kind: 'manual' as const, description: '계약 범위 확인' },
    ]
    const saved = {
      enabled: true,
      goal: { ...goal, title, version: 1, revision: 1, criteria, last_evidence: null },
    }
    putConversationGoal.mockImplementation(async () => {
      getConversationGoal.mockResolvedValue(saved)
      return saved
    })
    render(<ConversationGoalCard conversationId={conversationId} />)
    await user.click(await screen.findByRole('button', { name: '목표 만들기' }))
    await user.type(screen.getByRole('textbox', { name: '목표 이름' }), title)
    await user.type(screen.getByRole('textbox', { name: '결과 파일 경로' }), 'outputs/report.csv')
    await user.click(screen.getByRole('button', { name: '조건 추가' }))
    await user.selectOptions(screen.getAllByRole('combobox')[1], 'manual')
    await user.type(screen.getByRole('textbox', { name: /확인할 내용/ }), '계약 범위 확인')
    await user.click(screen.getByRole('button', { name: '저장' }))
    expect(putConversationGoal).toHaveBeenCalledWith(conversationId, {
      expected_revision: 0,
      title,
      criteria,
    })
    expect(await screen.findByText(title)).toBeVisible()
  })

  it('reviews a steer from the current conversation before saving the confirmed criterion', async () => {
    getConversationGoal.mockResolvedValue({ enabled: true, goal })
    const user = userEvent.setup()
    const steerText = '국내 업체만 비교'
    const criteria = [...goal.criteria, { kind: 'manual' as const, description: steerText }]
    const updated = {
      enabled: true,
      goal: { ...goal, version: 3, revision: 8, criteria, last_evidence: null },
    }
    putConversationGoal.mockImplementation(async () => {
      getConversationGoal.mockResolvedValue(updated)
      return updated
    })
    render(<ConversationGoalCard conversationId={conversationId} />)
    await screen.findByText(goal.title)
    const steer = (id: string) =>
      act(() => {
        window.dispatchEvent(
          new CustomEvent('moldy:review-steer-criterion', {
            detail: { conversationId: id, text: steerText },
          }),
        )
      })
    steer('conversation-b')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    steer(conversationId)
    expect(await screen.findByDisplayValue(steerText)).toBeVisible()
    expect(putConversationGoal).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '저장' }))
    expect(putConversationGoal).toHaveBeenCalledWith(conversationId, {
      expected_revision: goal.revision,
      title: goal.title,
      criteria,
    })
  })
})
