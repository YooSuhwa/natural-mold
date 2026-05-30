import { render, screen, userEvent, waitFor } from '../test-utils'
import SchedulesPage from '@/app/schedules/page'
import { mockTriggerList } from '../mocks/fixtures'

const mockRunNow = vi.fn()

vi.mock('@/lib/hooks/use-triggers', () => ({
  useAllTriggers: () => ({ data: mockTriggerList, isLoading: false }),
  useUpdateTriggerGlobal: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteTriggerGlobal: () => ({ mutate: vi.fn(), isPending: false }),
  useRunTriggerNow: () => ({ mutate: mockRunNow, isPending: false }),
  useTriggerRuns: () => ({ data: [], isLoading: false }),
}))

vi.mock('@/components/shared/delete-confirm-dialog', () => ({
  DeleteConfirmDialog: () => null,
}))

describe('SchedulesPage', () => {
  beforeEach(() => {
    mockRunNow.mockClear()
  })

  it('renders global schedule table', () => {
    render(<SchedulesPage />)

    expect(screen.getByRole('heading', { name: '스케줄' })).toBeInTheDocument()
    expect(screen.getByText('Hourly update')).toBeInTheDocument()
    expect(screen.getAllByText('Good morning report').length).toBeGreaterThan(0)
    expect(screen.getByText('전체 스케줄')).toBeInTheDocument()
  })

  it('runs a schedule from the global page', async () => {
    const user = userEvent.setup()
    render(<SchedulesPage />)

    await user.click(screen.getAllByRole('button', { name: '즉시 실행' })[0])

    await waitFor(() => {
      expect(mockRunNow).toHaveBeenCalledWith('trigger-1')
    })
  })
})
