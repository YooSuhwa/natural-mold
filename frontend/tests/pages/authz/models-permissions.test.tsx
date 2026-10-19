import { render, screen, userEvent } from '../../test-utils'
import ModelsPage from '@/app/settings/models/page'
import SystemLlmSettingsPage from '@/app/settings/system-llm/page'
import { mockModelList } from '../../mocks/fixtures'

const state = vi.hoisted(() => ({
  user: { id: 'u', is_super_user: false },
  models: [] as unknown[],
  replace: vi.fn(),
  settings: vi.fn(),
  edited: vi.fn(),
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({ data: state.user, isPending: false }),
}))
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: state.replace, push: vi.fn() }) }))
vi.mock('@/app/settings/_components/settings-shell', () => ({
  SettingsShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))
vi.mock('@/lib/hooks/use-models', () => ({
  useModels: () => ({ data: state.models, isLoading: false }),
  useDiscoverModels: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-health', () => ({
  useModelHealth: () => ({ data: [] }),
  useRunHealthCheck: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-credentials', () => ({
  useCredentials: () => ({ data: [] }),
  useSystemCredentials: () => ({ data: [] }),
}))
vi.mock('@/lib/hooks/use-system-llm-settings', () => ({
  useSystemLlmSettings: () => state.settings(),
  useUpdateSystemLlmSetting: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/components/model/model-add-dialog', () => ({ ModelAddDialog: () => null }))
vi.mock('@/components/model/model-test-dialog', () => ({ ModelTestDialog: () => null }))
vi.mock('@/components/model/model-test-bulk-dialog', () => ({ ModelTestBulkDialog: () => null }))
vi.mock('@/components/model/model-edit-dialog', () => ({
  ModelEditDialog: ({ open }: { open: boolean }) => {
    if (open) state.edited()
    return null
  },
}))

const models = mockModelList.map((m) => ({
  ...m,
  context_window: null,
  agent_count: 0,
  is_visible: true,
}))

describe('model catalog by role (plan 13.9 모델)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.models = models
  })

  it('operator: add and edit', async () => {
    state.user = { id: 'op', is_super_user: true }
    render(<ModelsPage />)
    expect(screen.getByRole('button', { name: /새 모델/ })).toBeInTheDocument()
    await userEvent.click(screen.getByText('GPT-4o'))
    expect(state.edited).toHaveBeenCalled()
  })

  it('member: sees allowed models but cannot add or edit', async () => {
    state.user = { id: 'u', is_super_user: false }
    render(<ModelsPage />)
    expect(screen.getByText('GPT-4o')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /새 모델/ })).toBeNull()
    await userEvent.click(screen.getByText('GPT-4o'))
    expect(state.edited).not.toHaveBeenCalled()
  })

  it('member with an empty catalog gets no add action', () => {
    state.user = { id: 'u', is_super_user: false }
    state.models = []
    render(<ModelsPage />)
    expect(screen.queryByRole('button', { name: /모델 추가/ })).toBeNull()
  })
})

describe('system LLM settings stay operator-only', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sends members away without loading settings', () => {
    state.user = { id: 'u', is_super_user: false }
    render(<SystemLlmSettingsPage />)
    expect(state.replace).toHaveBeenCalledWith('/')
    expect(state.settings).not.toHaveBeenCalled()
  })

  it('lets operators in', () => {
    state.user = { id: 'op', is_super_user: true }
    state.settings.mockReturnValue({ data: [], isLoading: false })
    render(<SystemLlmSettingsPage />)
    expect(state.settings).toHaveBeenCalled()
  })
})
