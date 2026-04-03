import { render, screen } from '../test-utils'
import ModelsPage from '@/app/models/page'
import { mockModelList } from '../mocks/fixtures'

vi.mock('next/link', () => ({
  default: ({
    children,
    href,
    ...props
  }: {
    children: React.ReactNode
    href: string
    [key: string]: unknown
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}))

const mockUseModels = vi.fn()
const mockCreateModel = vi.fn().mockResolvedValue({})
const mockDeleteModel = vi.fn()

vi.mock('@/lib/hooks/use-models', () => ({
  useModels: () => mockUseModels(),
  useCreateModel: () => ({
    mutateAsync: mockCreateModel,
    isPending: false,
  }),
  useDeleteModel: () => ({
    mutate: mockDeleteModel,
    isPending: false,
  }),
}))

// Mock Dialog components from base-ui which are complex
vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ children, open }: { children: React.ReactNode; open?: boolean }) => (
    <div data-testid="dialog" data-open={open}>
      {children}
    </div>
  ),
  DialogContent: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dialog-content">{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTrigger: ({ render }: { render: React.ReactNode }) => <>{render}</>,
}))

vi.mock('@/components/ui/select', () => ({
  Select: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectTrigger: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
  SelectValue: ({ placeholder }: { placeholder?: string }) => <span>{placeholder}</span>,
  SelectContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children, value }: { children: React.ReactNode; value: string }) => (
    <div data-value={value}>{children}</div>
  ),
}))

describe('ModelsPage', () => {
  beforeEach(() => {
    mockUseModels.mockReturnValue({ data: undefined, isLoading: false })
  })

  it('renders page header with title', () => {
    render(<ModelsPage />)
    expect(screen.getByText('모델 관리')).toBeInTheDocument()
  })

  it('renders loading skeletons', () => {
    mockUseModels.mockReturnValue({ data: undefined, isLoading: true })
    const { container } = render(<ModelsPage />)
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it('renders model list with provider info', () => {
    mockUseModels.mockReturnValue({ data: mockModelList, isLoading: false })
    render(<ModelsPage />)
    expect(screen.getByText('GPT-4o')).toBeInTheDocument()
    expect(screen.getByText('Claude Sonnet 4')).toBeInTheDocument()
    expect(screen.getByText('openai')).toBeInTheDocument()
    expect(screen.getByText('anthropic')).toBeInTheDocument()
  })

  it('shows default badge for default model', () => {
    mockUseModels.mockReturnValue({ data: mockModelList, isLoading: false })
    render(<ModelsPage />)
    expect(screen.getByText('기본')).toBeInTheDocument()
  })

  it('shows model name under display name', () => {
    mockUseModels.mockReturnValue({ data: mockModelList, isLoading: false })
    render(<ModelsPage />)
    expect(screen.getByText('gpt-4o')).toBeInTheDocument()
    expect(screen.getByText('claude-sonnet-4-20250514')).toBeInTheDocument()
  })

  it('shows provider icon abbreviations', () => {
    mockUseModels.mockReturnValue({ data: mockModelList, isLoading: false })
    render(<ModelsPage />)
    expect(screen.getByText('OAI')).toBeInTheDocument()
    expect(screen.getByText('ANT')).toBeInTheDocument()
  })

  it('shows add model button', () => {
    render(<ModelsPage />)
    // "모델 추가" appears in both trigger button and dialog title
    const addButtons = screen.getAllByText('모델 추가')
    expect(addButtons.length).toBeGreaterThanOrEqual(1)
  })

  it('shows empty state when no models', () => {
    mockUseModels.mockReturnValue({ data: [], isLoading: false })
    render(<ModelsPage />)
    expect(screen.getByText('등록된 모델이 없습니다.')).toBeInTheDocument()
  })

  it('renders add model dialog content', () => {
    // Dialog is always rendered (just controlled by open state)
    render(<ModelsPage />)
    expect(screen.getByText('새 LLM 모델을 등록합니다.')).toBeInTheDocument()
  })

  it('shows provider select options in dialog', () => {
    render(<ModelsPage />)
    expect(screen.getByText('OpenAI')).toBeInTheDocument()
    expect(screen.getByText('Anthropic')).toBeInTheDocument()
    expect(screen.getByText('Google')).toBeInTheDocument()
    expect(screen.getByText('기타')).toBeInTheDocument()
  })

  it('shows model form fields in dialog', () => {
    render(<ModelsPage />)
    expect(screen.getByPlaceholderText('gpt-4o')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('GPT-4o')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('https://api.openai.com/v1')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('sk-xxxxxxxxxxxx')).toBeInTheDocument()
  })

  it('shows delete button for each model', () => {
    mockUseModels.mockReturnValue({ data: mockModelList, isLoading: false })
    render(<ModelsPage />)
    expect(screen.getByLabelText('GPT-4o 삭제')).toBeInTheDocument()
    expect(screen.getByLabelText('Claude Sonnet 4 삭제')).toBeInTheDocument()
  })

  it('calls deleteModel when delete button clicked', async () => {
    mockUseModels.mockReturnValue({ data: mockModelList, isLoading: false })
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    render(<ModelsPage />)
    await user.click(screen.getByLabelText('GPT-4o 삭제'))
    expect(mockDeleteModel).toHaveBeenCalledWith('model-1')
  })

  it('calls createModel when form submitted', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    render(<ModelsPage />)

    // Fill model name (required field)
    const modelNameInput = screen.getByPlaceholderText('gpt-4o')
    await user.type(modelNameInput, 'gpt-4o-mini')

    // Fill display name
    const displayNameInput = screen.getByPlaceholderText('GPT-4o')
    await user.type(displayNameInput, 'GPT-4o Mini')

    // Submit
    const submitButton = screen.getByRole('button', { name: '등록' })
    await user.click(submitButton)

    expect(mockCreateModel).toHaveBeenCalledWith(
      expect.objectContaining({
        provider: 'openai',
        model_name: 'gpt-4o-mini',
        display_name: 'GPT-4o Mini',
      }),
    )
  })

  it('shows Google provider icon', () => {
    const modelsWithGoogle = [
      ...mockModelList,
      {
        ...mockModelList[0],
        id: 'model-3',
        provider: 'google',
        model_name: 'gemini-2.0',
        display_name: 'Gemini 2.0',
        is_default: false,
      },
    ]
    mockUseModels.mockReturnValue({ data: modelsWithGoogle, isLoading: false })
    render(<ModelsPage />)
    expect(screen.getByText('GGL')).toBeInTheDocument()
  })

  it('shows AI icon for custom provider', () => {
    const modelsWithCustom = [
      ...mockModelList,
      {
        ...mockModelList[0],
        id: 'model-4',
        provider: 'custom',
        model_name: 'my-model',
        display_name: 'Custom Model',
        is_default: false,
      },
    ]
    mockUseModels.mockReturnValue({ data: modelsWithCustom, isLoading: false })
    render(<ModelsPage />)
    expect(screen.getByText('AI')).toBeInTheDocument()
  })
})
