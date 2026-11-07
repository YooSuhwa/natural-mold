import { render, screen, userEvent, waitFor } from '../test-utils'
import SettingsPage from '@/app/settings/page'
import SecuritySettingsPage from '@/app/settings/security/page'
import AppearanceSettingsPage from '@/app/settings/appearance/page'
import AgentApiSettingsPage from '@/app/settings/agent-api/page'

const updateProfile = vi.fn()
const uploadAvatarImage = vi.fn()
const deleteAvatarImage = vi.fn()
const mockUseSession = vi.fn()
const mockUseAgentDeploymentCandidates = vi.fn()
const mockUseAgentDeployments = vi.fn()
const mockUseAgentApiKeys = vi.fn()
const mockCreateAgentDeployment = vi.fn()
const mockCreateAgentApiKey = vi.fn()
const mockRevokeAgentApiKey = vi.fn()

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
  useAgentDeploymentCandidates: () => mockUseAgentDeploymentCandidates(),
  useAgentDeployments: () => mockUseAgentDeployments(),
  useAgentApiKeys: () => mockUseAgentApiKeys(),
  useCreateAgentDeployment: () => mockCreateAgentDeployment(),
  useCreateAgentApiKey: () => mockCreateAgentApiKey(),
  useRevokeAgentApiKey: () => mockRevokeAgentApiKey(),
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
    mockUseAgentDeploymentCandidates.mockReturnValue({ data: [], isLoading: false })
    mockUseAgentDeployments.mockReturnValue({ data: [], isLoading: false })
    mockUseAgentApiKeys.mockReturnValue({ data: [], isLoading: false })
    mockCreateAgentDeployment.mockReturnValue({ mutateAsync: vi.fn(), isPending: false })
    mockCreateAgentApiKey.mockReturnValue({ mutateAsync: vi.fn(), isPending: false })
    mockRevokeAgentApiKey.mockReturnValue({ mutateAsync: vi.fn(), isPending: false })
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

    expect(screen.getByRole('heading', { name: '프로필' })).toBeInTheDocument()
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
      screen.getByText(
        '에이전트를 외부 시스템에서 호출할 수 있도록 배포하고 서버용 API 키를 발급합니다.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('배포 후보')).toBeInTheDocument()
    expect(screen.getAllByText('API 키').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('호출 예시')).toBeInTheDocument()
  })

  it('translates Agent API deployment candidate reason codes', () => {
    mockUseAgentDeploymentCandidates.mockReturnValue({
      data: [
        {
          agent_id: 'agent-1',
          agent_name: '리서치 에이전트',
          runtime_name: 'agent_runtime_1',
          existing_deployment_id: null,
          existing_public_id: null,
          eligible: false,
          ineligible_reason: null,
          ineligible_reason_code: 'fixed_identity_required',
        },
      ],
      isLoading: false,
    })

    render(<AgentApiSettingsPage />)

    expect(
      screen.getByText('API 배포에는 에이전트 고정 credential 사용 방식이 필요합니다.'),
    ).toBeInTheDocument()
    expect(screen.queryByText('API deployment requires fixed identity.')).not.toBeInTheDocument()
  })

  it('shows the admin settings section for super users', () => {
    render(<SettingsPage />)

    expect(screen.getAllByText('관리자').length).toBeGreaterThanOrEqual(1)
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
})
