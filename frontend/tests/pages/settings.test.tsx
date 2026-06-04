import { render, screen } from '../test-utils'
import SettingsPage from '@/app/settings/page'
import SecuritySettingsPage from '@/app/settings/security/page'
import AppearanceSettingsPage from '@/app/settings/appearance/page'
import AgentApiSettingsPage from '@/app/settings/agent-api/page'

vi.mock('next-themes', () => ({
  useTheme: () => ({
    theme: 'system',
    setTheme: vi.fn(),
  }),
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({
    data: {
      id: 'user-1',
      name: 'Test User',
      email: 'test@example.com',
      is_super_user: true,
      created_at: '2026-05-01T00:00:00Z',
      last_login_at: '2026-05-02T00:00:00Z',
    },
    isPending: false,
  }),
}))

describe('settings pages', () => {
  it('renders profile settings from the active session', () => {
    render(<SettingsPage />)

    expect(screen.getByRole('heading', { name: '설정' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '프로필' })).toHaveAttribute('href', '/settings')
    expect(screen.getByText('Test User')).toBeInTheDocument()
    expect(screen.getByText('test@example.com')).toBeInTheDocument()
    expect(screen.getByText('관리자')).toBeInTheDocument()
    expect(screen.queryByText('수화')).not.toBeInTheDocument()
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

  it('renders the Agent API placeholder page', () => {
    render(<AgentApiSettingsPage />)

    expect(screen.getByRole('heading', { name: 'Agent API' })).toBeInTheDocument()
    expect(
      screen.getByText('외부 앱에서 Moldy 에이전트를 호출하기 위한 API 배포와 키 관리는 준비 중입니다.'),
    ).toBeInTheDocument()
  })
})
