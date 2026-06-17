import { fireEvent } from '@testing-library/react'
import { render, screen, userEvent, waitFor } from '../../test-utils'
import { ScheduleForm } from '@/features/schedules/components/schedule-form'

vi.mock('@/lib/hooks/use-conversations', () => ({
  useConversations: () => ({
    data: [
      {
        id: 'conv-1',
        title: '주말 여행 상담',
        updated_at: '2026-05-30T03:00:00Z',
        unread_count: 0,
      },
      {
        id: 'conv-2',
        title: null,
        updated_at: '2026-05-29T03:00:00Z',
        unread_count: 2,
      },
    ],
  }),
}))

describe('ScheduleForm', () => {
  it('creates a one-time schedule request', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()

    render(<ScheduleForm onSubmit={onSubmit} onCancel={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: '1회' }))
    await user.type(screen.getByPlaceholderText('예: 매일 아침 뉴스 요약'), '한 번만 실행')
    await user.type(
      screen.getByPlaceholderText('각 실행 시 에이전트에 전달할 메시지...'),
      '테스트 메시지',
    )
    fireEvent.change(screen.getByLabelText('실행 시각 (KST)'), {
      target: { value: '2030-01-02T09:30' },
    })
    await user.click(screen.getByRole('button', { name: '스케줄 생성' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        name: '한 번만 실행',
        trigger_type: 'one_time',
        schedule_config: {
          scheduled_at: new Date('2030-01-02T09:30').toISOString(),
        },
        input_message: '테스트 메시지',
        timezone: 'Asia/Seoul',
        conversation_policy: 'schedule_thread',
      }),
    )
  })

  it('selects an existing conversation by title instead of requiring a raw id', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()

    render(<ScheduleForm agentId="agent-1" onSubmit={onSubmit} onCancel={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: '기존 대화에 저장' }))

    expect(screen.queryByPlaceholderText('기존 대화 ID')).not.toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: '기존 대화 선택' }))
    await user.click(screen.getByRole('option', { name: /주말 여행 상담/ }))
    await user.click(screen.getByRole('button', { name: '스케줄 생성' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        conversation_policy: 'selected_conversation',
        target_conversation_id: 'conv-1',
      }),
    )
  })

  it('can return max run and auto-pause limits back to unlimited', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()

    render(<ScheduleForm onSubmit={onSubmit} onCancel={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: '최대 실행 횟수 제한' }))
    await user.clear(screen.getByLabelText('최대 실행 횟수'))
    await user.type(screen.getByLabelText('최대 실행 횟수'), '3')
    await user.click(screen.getByRole('button', { name: '최대 실행 횟수 무제한' }))

    await user.click(screen.getByRole('button', { name: '실패 후 정지 사용' }))
    await user.clear(screen.getByLabelText('실패 허용 횟수'))
    await user.type(screen.getByLabelText('실패 허용 횟수'), '2')
    await user.click(screen.getByRole('button', { name: '실패 후 정지 사용 안 함' }))

    await user.click(screen.getByRole('button', { name: '스케줄 생성' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        max_runs: null,
        auto_pause_after_failures: null,
      }),
    )
  })
})
