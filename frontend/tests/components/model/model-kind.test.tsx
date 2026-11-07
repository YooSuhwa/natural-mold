import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { ModelAddDialog } from '@/components/model/model-add-dialog'
import { ModelEditDialog } from '@/components/model/model-edit-dialog'
import { mockModel } from '../../mocks/fixtures'

const hooks = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  discover: vi.fn(),
}))

vi.mock('@/lib/hooks/use-models', () => ({
  useCreateModel: () => ({ mutateAsync: hooks.create, isPending: false }),
  useUpdateModel: () => ({ mutateAsync: hooks.update, isPending: false }),
  useDeleteModel: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDiscoverModels: () => ({ mutateAsync: hooks.discover, isPending: false }),
  useTestModel: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-credentials', () => ({
  useCredentials: () => ({
    data: [{ id: 'cred-1', name: 'OpenAI 키', definition_key: 'openai' }],
  }),
  useCredentialTypes: () => ({
    data: [{ key: 'openai', category: 'llm', display_name: 'OpenAI' }],
  }),
}))
vi.mock('@/lib/hooks/use-health', () => ({
  useModelHealth: () => ({ data: [] }),
  useRunHealthCheck: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/components/shared/health-history-chart', () => ({ HealthHistoryChart: () => null }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

async function chooseKind(name: string) {
  await userEvent.click(screen.getByTestId('model-add-kind-trigger'))
  await userEvent.click(await screen.findByRole('option', { name }))
}

describe('model kind selector (Q14)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    hooks.create.mockResolvedValue(mockModel)
    hooks.update.mockResolvedValue(mockModel)
    hooks.discover.mockResolvedValue([])
  })

  it('requires dimensions for a custom embedding model and sends kind with them', async () => {
    render(<ModelAddDialog open onOpenChange={vi.fn()} />)
    await chooseKind('임베딩 모델')
    await userEvent.click(screen.getByRole('tab', { name: '사용자 지정 ID' }))
    await userEvent.type(screen.getByLabelText(/모델 ID/), 'text-embedding-3-large')

    const dimensions = screen.getByLabelText(/차원/)
    expect(dimensions).toHaveValue(1024)
    // The chat connection test does not apply to an embedding model.
    expect(screen.queryByTestId('custom-test-button')).not.toBeInTheDocument()

    const save = screen.getByRole('button', { name: '모델 저장' })
    await userEvent.clear(dimensions)
    expect(save).toBeDisabled()
    expect(screen.getByText('1~4096 사이의 정수를 넣으세요.')).toBeInTheDocument()
    await userEvent.type(dimensions, '5000')
    expect(save).toBeDisabled()
    await userEvent.clear(dimensions)
    await userEvent.type(dimensions, '3072')
    await userEvent.click(save)

    await waitFor(() =>
      expect(hooks.create).toHaveBeenCalledWith(
        expect.objectContaining({
          model_name: 'text-embedding-3-large',
          kind: 'embedding',
          embedding_dimensions: 3072,
        }),
      ),
    )
  })

  it('creates a chat model with no dimensions by default', async () => {
    render(<ModelAddDialog open onOpenChange={vi.fn()} />)
    await userEvent.click(screen.getByRole('tab', { name: '사용자 지정 ID' }))
    expect(screen.queryByLabelText(/차원/)).not.toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(/모델 ID/), 'gpt-5')
    await userEvent.click(screen.getByRole('button', { name: '모델 저장' }))
    await waitFor(() =>
      expect(hooks.create).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'chat', embedding_dimensions: null }),
      ),
    )
  })

  it('discovers with the selected kind and registers picks as that kind', async () => {
    hooks.discover.mockResolvedValue([
      {
        model_name: 'text-embedding-3-small',
        display_name: 'Embedding Small',
        source: 'litellm',
        provider: 'openai',
        context_window: null,
        max_output_tokens: null,
        cost_per_input_token: null,
        cost_per_output_token: null,
        input_modalities: null,
        output_modalities: null,
        supports_vision: null,
        supports_function_calling: null,
        supports_reasoning: null,
        already_registered: false,
        rankings: null,
      },
    ])
    render(<ModelAddDialog open onOpenChange={vi.fn()} />)
    await chooseKind('임베딩 모델')
    await userEvent.click(screen.getByRole('combobox', { name: 'LLM 자격증명' }))
    await userEvent.click(await screen.findByRole('option', { name: /OpenAI 키/ }))
    await userEvent.click(screen.getByRole('button', { name: '탐색' }))

    await waitFor(() =>
      expect(hooks.discover).toHaveBeenCalledWith({ credentialId: 'cred-1', kind: 'embedding' }),
    )
    const list = await screen.findByRole('list')
    await userEvent.click(within(list).getByRole('checkbox'))
    expect(within(list).queryByRole('button', { name: /테스트/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '선택 항목 저장' }))
    await waitFor(() =>
      expect(hooks.create).toHaveBeenCalledWith(
        expect.objectContaining({
          model_name: 'text-embedding-3-small',
          kind: 'embedding',
          embedding_dimensions: 1024,
        }),
      ),
    )
  })

  it('disables the default toggle for an embedding model and saves it as not default', async () => {
    render(
      <ModelEditDialog model={{ ...mockModel, is_default: true }} open onOpenChange={vi.fn()} />,
    )
    const defaultToggle = screen.getByRole('checkbox', { name: /기본/ })
    expect(defaultToggle).toBeChecked()
    expect(defaultToggle).not.toHaveAttribute('aria-disabled', 'true')

    await userEvent.click(screen.getByTestId('e-kind-trigger'))
    await userEvent.click(await screen.findByRole('option', { name: '임베딩 모델' }))
    expect(screen.getByRole('checkbox', { name: /기본/ })).not.toBeChecked()
    // Base UI checkboxes mark disabled with aria-disabled.
    expect(screen.getByRole('checkbox', { name: /기본/ })).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText('임베딩 모델은 기본 모델로 정할 수 없습니다.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '변경사항 저장' }))
    await waitFor(() =>
      expect(hooks.update).toHaveBeenCalledWith({
        id: mockModel.id,
        data: expect.objectContaining({
          kind: 'embedding',
          embedding_dimensions: 1024,
          is_default: false,
        }),
      }),
    )
  })
})
