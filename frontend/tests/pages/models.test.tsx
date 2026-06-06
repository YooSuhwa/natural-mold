import { render, screen } from '../test-utils'
import ModelsPage from '@/app/settings/models/page'
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

/**
 * 페이지 구조 (M10 이후): PageHeader(영문 "Models" 제목 + New model 버튼) +
 * DataTable + EmptyState. 옛 테스트는 한국어 i18n + Tabs 구조를 가정했지만
 * 현재 페이지는 i18n 미적용 영문 + 탭 없음. provider/모델 detail은
 * model-detail-modal 컴포넌트 단위 테스트로 분리.
 */
describe('ModelsPage', () => {
  beforeEach(() => {
    mockUseModels.mockReturnValue({ data: undefined, isLoading: false })
  })

  it('renders page header with title + 새 모델 action', () => {
    render(<ModelsPage />)
    expect(screen.getByText('모델')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /새 모델/ })).toBeInTheDocument()
  })

  it('shows empty state when no models', () => {
    mockUseModels.mockReturnValue({ data: [], isLoading: false })
    render(<ModelsPage />)
    expect(screen.getByText('아직 모델이 없어요')).toBeInTheDocument()
  })

  it('renders model display names when data is loaded', () => {
    mockUseModels.mockReturnValue({ data: modelsWithNewFields, isLoading: false })
    render(<ModelsPage />)
    expect(screen.getByText('GPT-4o')).toBeInTheDocument()
    expect(screen.getByText('Claude Sonnet 4')).toBeInTheDocument()
  })

  it('uses compact pricing and action columns to avoid horizontal overflow', () => {
    mockUseModels.mockReturnValue({ data: modelsWithNewFields, isLoading: false })
    render(<ModelsPage />)

    expect(screen.getByRole('columnheader', { name: /단가/ })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: '입력 단가' })).not.toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: '출력 단가' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /상태 확인/ })[0]).toHaveClass('px-2')
  })

  // 페이지 안의 DataTable / 모델 detail / provider 카드 / delete 흐름은
  // model-* 컴포넌트 단위 테스트와 e2e가 책임진다 (페이지 단위에서 제외).
})
