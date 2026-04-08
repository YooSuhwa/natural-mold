import { render, screen } from '../test-utils'
import userEvent from '@testing-library/user-event'
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
const mockDeleteModel = vi.fn()

vi.mock('@/lib/hooks/use-models', () => ({
  useModels: () => mockUseModels(),
  useUpdateModel: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useDeleteModel: () => ({
    mutate: mockDeleteModel,
    isPending: false,
  }),
}))

vi.mock('@/lib/hooks/use-providers', () => ({
  useProviders: () => ({ data: [], isLoading: false }),
  useDeleteProvider: () => ({ mutate: vi.fn(), isPending: false }),
}))

// Mock complex sub-components
vi.mock('@/components/model/provider-card', () => ({
  ProviderCard: () => <div data-testid="provider-card" />,
}))

vi.mock('@/components/model/provider-form', () => ({
  ProviderForm: () => null,
}))

vi.mock('@/components/model/model-add-dialog', () => ({
  ModelAddDialog: () => null,
}))

vi.mock('@/components/model/model-detail-modal', () => ({
  ModelDetailModal: () => null,
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
}))

vi.mock('@/components/ui/alert-dialog', () => ({
  AlertDialog: ({ children, open }: { children: React.ReactNode; open?: boolean }) => (
    <div data-testid="alert-dialog" data-open={open}>
      {children}
    </div>
  ),
  AlertDialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertDialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertDialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  AlertDialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  AlertDialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertDialogAction: ({
    children,
    onClick,
  }: {
    children: React.ReactNode
    onClick?: () => void
    variant?: string
  }) => <button onClick={onClick}>{children}</button>,
  AlertDialogCancel: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
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

// Extend mockModelList with new required fields
const modelsWithNewFields = mockModelList.map((m) => ({
  ...m,
  provider_id: `provider-${m.provider}`,
  provider_name: m.provider === 'openai' ? 'OpenAI' : 'Anthropic',
  context_window: null,
  max_output_tokens: null,
  input_modalities: null,
  output_modalities: null,
  supports_vision: null,
  supports_function_calling: null,
  supports_reasoning: null,
  agent_count: 0,
}))

async function switchToModelsTab() {
  const user = userEvent.setup()
  const modelsTab = screen.getByRole('tab', { name: 'Models' })
  await user.click(modelsTab)
}

describe('ModelsPage', () => {
  beforeEach(() => {
    mockUseModels.mockReturnValue({ data: undefined, isLoading: false })
  })

  it('renders page header with title', () => {
    render(<ModelsPage />)
    expect(screen.getByText('모델 관리')).toBeInTheDocument()
  })

  it('renders loading skeletons for models', async () => {
    mockUseModels.mockReturnValue({ data: undefined, isLoading: true })
    const { container } = render(<ModelsPage />)
    await switchToModelsTab()
    const skeletons = container.querySelectorAll("[data-slot='skeleton']")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it('renders model list with provider info', async () => {
    mockUseModels.mockReturnValue({ data: modelsWithNewFields, isLoading: false })
    render(<ModelsPage />)
    await switchToModelsTab()
    expect(screen.getByText('GPT-4o')).toBeInTheDocument()
    expect(screen.getByText('Claude Sonnet 4')).toBeInTheDocument()
  })

  it('shows default badge for default model', async () => {
    mockUseModels.mockReturnValue({ data: modelsWithNewFields, isLoading: false })
    render(<ModelsPage />)
    await switchToModelsTab()
    expect(screen.getByText('기본')).toBeInTheDocument()
  })

  it('shows model name under display name', async () => {
    mockUseModels.mockReturnValue({ data: modelsWithNewFields, isLoading: false })
    render(<ModelsPage />)
    await switchToModelsTab()
    expect(screen.getByText('gpt-4o')).toBeInTheDocument()
    expect(screen.getByText('claude-sonnet-4-20250514')).toBeInTheDocument()
  })

  it('shows provider icon abbreviations', async () => {
    mockUseModels.mockReturnValue({ data: modelsWithNewFields, isLoading: false })
    render(<ModelsPage />)
    await switchToModelsTab()
    expect(screen.getByText('OAI')).toBeInTheDocument()
    expect(screen.getByText('ANT')).toBeInTheDocument()
  })

  it('shows add model button in models tab', async () => {
    render(<ModelsPage />)
    await switchToModelsTab()
    expect(screen.getByText('모델 추가')).toBeInTheDocument()
  })

  it('shows empty state when no models', async () => {
    mockUseModels.mockReturnValue({ data: [], isLoading: false })
    render(<ModelsPage />)
    await switchToModelsTab()
    expect(screen.getByText('등록된 모델이 없습니다.')).toBeInTheDocument()
  })

  it('shows delete button for each model', async () => {
    mockUseModels.mockReturnValue({ data: modelsWithNewFields, isLoading: false })
    render(<ModelsPage />)
    await switchToModelsTab()
    expect(screen.getByLabelText('GPT-4o 삭제')).toBeInTheDocument()
    expect(screen.getByLabelText('Claude Sonnet 4 삭제')).toBeInTheDocument()
  })

  it('shows Google provider icon', async () => {
    const modelsWithGoogle = [
      ...modelsWithNewFields,
      {
        ...modelsWithNewFields[0],
        id: 'model-3',
        provider: 'google',
        provider_id: 'provider-google',
        provider_name: 'Google',
        model_name: 'gemini-2.0',
        display_name: 'Gemini 2.0',
        is_default: false,
      },
    ]
    mockUseModels.mockReturnValue({ data: modelsWithGoogle, isLoading: false })
    render(<ModelsPage />)
    await switchToModelsTab()
    expect(screen.getByText('GGL')).toBeInTheDocument()
  })

  it('shows AI icon for custom provider', async () => {
    const modelsWithCustom = [
      ...modelsWithNewFields,
      {
        ...modelsWithNewFields[0],
        id: 'model-4',
        provider: 'custom',
        provider_id: 'provider-custom',
        provider_name: 'Custom',
        model_name: 'my-model',
        display_name: 'Custom Model',
        is_default: false,
      },
    ]
    mockUseModels.mockReturnValue({ data: modelsWithCustom, isLoading: false })
    render(<ModelsPage />)
    await switchToModelsTab()
    expect(screen.getByText('AI')).toBeInTheDocument()
  })
})
