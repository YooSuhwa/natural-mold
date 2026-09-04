import { render, screen, userEvent } from '../test-utils'
import ManualCreationPage from '@/app/agents/new/manual/page'

const mockUseModels = vi.fn()
const mockRefetchModels = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ back: vi.fn(), replace: vi.fn() }),
}))

vi.mock('@/lib/hooks/use-models', () => ({
  useModels: () => mockUseModels(),
}))

vi.mock('@/lib/hooks/use-agents', () => ({
  useCreateAgent: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

vi.mock('@/lib/hooks/use-tools', () => ({
  useTools: () => ({ data: [] }),
}))

vi.mock('@/lib/hooks/use-skills', () => ({
  useSkills: () => ({ data: [] }),
}))

vi.mock('@/lib/hooks/use-middlewares', () => ({
  useMiddlewares: () => ({ data: [] }),
}))

describe('ManualCreationPage model availability', () => {
  beforeEach(() => {
    mockRefetchModels.mockReset()
  })

  it.each([
    ['a terminal model-query error', { data: undefined, isLoading: false, isError: true }],
    ['an empty loaded model list', { data: [], isLoading: false, isError: false }],
  ])('renders a retryable error surface for %s', async (_scenario, queryState) => {
    const user = userEvent.setup()
    mockUseModels.mockReturnValue({ ...queryState, refetch: mockRefetchModels })

    render(<ManualCreationPage />)

    expect(screen.getByRole('heading', { name: '문제가 발생했습니다' })).toBeInTheDocument()
    expect(
      screen.getByText('모델 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.'),
    ).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('에이전트 이름')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '다시 시도' }))

    expect(mockRefetchModels).toHaveBeenCalledOnce()
  })
})
