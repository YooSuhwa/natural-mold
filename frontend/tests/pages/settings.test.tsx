import { render, screen, userEvent, waitFor } from '../test-utils'
import SettingsPage from '@/app/settings/page'
import SecuritySettingsPage from '@/app/settings/security/page'
import AppearanceSettingsPage from '@/app/settings/appearance/page'
import AgentApiSettingsPage from '@/app/settings/agent-api/page'
import MemorySettingsPage from '@/app/settings/memory/page'

const updateProfile = vi.fn()
const uploadAvatarImage = vi.fn()
const deleteAvatarImage = vi.fn()
const updateUserMemorySettings = vi.fn()
const createMemory = vi.fn()
const updateMemory = vi.fn()
const deleteMemory = vi.fn()
const mockUseSession = vi.fn()

vi.mock('@/lib/api/auth', () => ({
  authApi: {
    updateProfile: (...args: unknown[]) => updateProfile(...args),
    uploadAvatarImage: (...args: unknown[]) => uploadAvatarImage(...args),
    deleteAvatarImage: (...args: unknown[]) => deleteAvatarImage(...args),
  },
}))

vi.mock('next-themes', () => ({
  useTheme: () => ({
    theme: 'system',
    setTheme: vi.fn(),
  }),
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: () => mockUseSession(),
}))

vi.mock('@/lib/hooks/use-agent-api', () => ({
  useAgentDeploymentCandidates: () => ({ data: [], isLoading: false }),
  useAgentDeployments: () => ({ data: [], isLoading: false }),
  useAgentApiKeys: () => ({ data: [], isLoading: false }),
  useCreateAgentDeployment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateAgentApiKey: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRevokeAgentApiKey: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgents: () => ({
    data: [
      {
        id: 'agent-1',
        name: '리서치 에이전트',
        description: '뉴스를 요약합니다.',
        status: 'active',
        is_favorite: false,
        image_url: null,
        model_display_name: 'GPT-5',
        tool_count: 0,
        fallback_count: 0,
        created_at: '2026-05-01T00:00:00Z',
        updated_at: '2026-05-01T00:00:00Z',
        unread_count: 0,
      },
    ],
    isLoading: false,
  }),
}))

vi.mock('@/lib/hooks/use-memory', () => ({
  useUserMemorySettings: () => ({
    data: {
      memory_enabled: true,
      memory_read_enabled: true,
      memory_write_policy: 'ask',
      allowed_scopes: 'both',
      trigger_memory_write_policy: 'off',
    },
    isLoading: false,
  }),
  useUpdateUserMemorySettings: () => ({
    mutateAsync: updateUserMemorySettings,
    isPending: false,
  }),
  useMemories: () => ({
    data: [
      {
        id: 'memory-1',
        user_id: 'user-1',
        agent_id: null,
        scope: 'user',
        content: '회의는 오후 3시 이후를 선호합니다.',
        reason: '일정 선호',
        store_path: '/memories/users/user-1/memory-1.md',
        source_conversation_id: null,
        source_message_id: null,
        source_run_id: null,
        status: 'active',
        created_at: '2026-06-03T00:00:00Z',
        updated_at: '2026-06-03T00:00:00Z',
        deleted_at: null,
      },
    ],
    isLoading: false,
  }),
  useCreateMemory: () => ({ mutateAsync: createMemory, isPending: false }),
  useUpdateMemory: () => ({ mutateAsync: updateMemory, isPending: false }),
  useDeleteMemory: () => ({ mutateAsync: deleteMemory, isPending: false }),
}))

describe('settings pages', () => {
  beforeEach(() => {
    updateProfile.mockClear()
    updateProfile.mockResolvedValue({
      id: 'user-1',
      name: 'Test User',
      display_name: '새이름',
      avatar_mode: 'initials',
      avatar_initials: '새',
      avatar_color: 'sky',
      avatar_image_url: null,
      email: 'test@example.com',
      is_super_user: true,
      created_at: '2026-05-01T00:00:00Z',
      last_login_at: '2026-05-02T00:00:00Z',
    })
    uploadAvatarImage.mockClear()
    deleteAvatarImage.mockClear()
    updateUserMemorySettings.mockClear()
    createMemory.mockClear()
    updateMemory.mockClear()
    deleteMemory.mockClear()
    mockUseSession.mockReturnValue({
      data: {
        id: 'user-1',
        name: 'Test User',
        display_name: '체스터',
        avatar_mode: 'initials',
        avatar_initials: '체',
        avatar_color: 'sky',
        avatar_image_url: null,
        email: 'test@example.com',
        is_super_user: true,
        created_at: '2026-05-01T00:00:00Z',
        last_login_at: '2026-05-02T00:00:00Z',
      },
      isPending: false,
    })
  })

  it('renders editable profile settings from the active session', () => {
    render(<SettingsPage />)

    expect(screen.getByRole('heading', { name: '설정' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '프로필' })).toHaveAttribute('href', '/settings')
    expect(screen.getByDisplayValue('체스터')).toBeInTheDocument()
    expect(screen.getByLabelText('체스터 프로필 아이콘')).toHaveTextContent('체')
    expect(screen.getByDisplayValue('체')).toBeInTheDocument()
    expect(screen.getByText('test@example.com')).toBeInTheDocument()
    expect(screen.getAllByText('관리자').length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText('수화')).not.toBeInTheDocument()
  })

  it('saves display name and letter avatar settings', async () => {
    render(<SettingsPage />)

    await userEvent.clear(screen.getByLabelText('표시 이름'))
    await userEvent.type(screen.getByLabelText('표시 이름'), '새이름')
    await userEvent.clear(screen.getByLabelText('아이콘 문자'))
    await userEvent.type(screen.getByLabelText('아이콘 문자'), '새')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))

    await waitFor(() => {
      expect(updateProfile).toHaveBeenCalledWith({
        display_name: '새이름',
        avatar_mode: 'initials',
        avatar_initials: '새',
        avatar_color: 'sky',
      })
    })
  })

  it('renders the security placeholder page', () => {
    render(<SecuritySettingsPage />)

    expect(screen.getByRole('heading', { name: '보안' })).toBeInTheDocument()
    expect(screen.getByText('비밀번호 변경과 세션 관리는 준비 중입니다.')).toBeInTheDocument()
  })

  it('renders appearance and language settings', () => {
    render(<AppearanceSettingsPage />)

    expect(screen.getByRole('heading', { name: '화면 및 언어' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /라이트/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /다크/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /시스템/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /한국어/ })).toBeInTheDocument()
  })

  it('renders the Agent API management page', () => {
    render(<AgentApiSettingsPage />)

    expect(screen.getByRole('heading', { name: 'Agent API' })).toBeInTheDocument()
    expect(
      screen.getByText('Deploy agents, issue server-side API keys, and call Moldy from external systems.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Deployment candidates')).toBeInTheDocument()
    expect(screen.getAllByText('API keys').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Call examples')).toBeInTheDocument()
  })

  it('shows the admin settings section for super users', () => {
    render(<SettingsPage />)

    expect(screen.getAllByText('관리자').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole('link', { name: '운영자 관리' })).toHaveAttribute(
      'href',
      '/settings/marketplace-admin',
    )
    expect(screen.getByRole('link', { name: '시스템 자격증명' })).toHaveAttribute(
      'href',
      '/settings/system-credentials',
    )
    expect(screen.getByRole('link', { name: '시스템 LLM 설정' })).toHaveAttribute(
      'href',
      '/settings/system-llm',
    )
    expect(screen.getByRole('link', { name: '전체 활동 기록' })).toHaveAttribute(
      'href',
      '/settings/admin/audit',
    )
  })

  it('hides the admin settings section for regular users', () => {
    mockUseSession.mockReturnValue({
      data: {
        id: 'user-2',
        name: 'Regular User',
        display_name: '일반 사용자',
        avatar_mode: 'initials',
        avatar_initials: '일',
        avatar_color: 'mint',
        avatar_image_url: null,
        email: 'regular@example.com',
        is_super_user: false,
        created_at: '2026-05-01T00:00:00Z',
        last_login_at: '2026-05-02T00:00:00Z',
      },
      isPending: false,
    })

    render(<SettingsPage />)

    expect(screen.queryByText('관리자')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '운영자 관리' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '시스템 자격증명' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '시스템 LLM 설정' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '전체 활동 기록' })).not.toBeInTheDocument()
  })

  it('renders memory policy controls and recorded memories', () => {
    render(<MemorySettingsPage />)

    expect(screen.getByRole('heading', { name: '메모리' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '메모리' })).toHaveAttribute(
      'href',
      '/settings/memory',
    )
    expect(screen.getByLabelText('메모리 활성화')).toBeChecked()
    expect(screen.getByLabelText('응답에 메모리 사용')).toBeChecked()
    expect(screen.getByText('저장 전 확인')).toBeInTheDocument()
    expect(screen.getByText('사용자 + 에이전트')).toBeInTheDocument()
    expect(screen.getByText('회의는 오후 3시 이후를 선호합니다.')).toBeInTheDocument()
  })

  it('creates a memory from the settings page', async () => {
    createMemory.mockResolvedValue({
      id: 'memory-2',
      user_id: 'user-1',
      agent_id: null,
      scope: 'user',
      content: '문서 초안은 한국어로 먼저 작성합니다.',
      reason: null,
      store_path: '/memories/users/user-1/memory-2.md',
      source_conversation_id: null,
      source_message_id: null,
      source_run_id: null,
      status: 'active',
      created_at: '2026-06-04T00:00:00Z',
      updated_at: '2026-06-04T00:00:00Z',
      deleted_at: null,
    })
    render(<MemorySettingsPage />)

    await userEvent.type(
      screen.getByLabelText('새 메모리 내용'),
      '문서 초안은 한국어로 먼저 작성합니다.',
    )
    await userEvent.click(screen.getByRole('button', { name: '메모리 추가' }))

    await waitFor(() => {
      expect(createMemory).toHaveBeenCalledWith({
        scope: 'user',
        content: '문서 초안은 한국어로 먼저 작성합니다.',
        reason: null,
        agent_id: null,
      })
    })
  })
})
