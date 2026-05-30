import { fireEvent } from '@testing-library/react'
import { render, screen, userEvent, waitFor } from '../../test-utils'
import { ScheduleForm } from '@/components/agent/visual-settings/dialogs/schedule-dialog'

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
})
