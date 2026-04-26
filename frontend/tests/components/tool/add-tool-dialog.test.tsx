import { render, screen, userEvent } from '../../test-utils'
import { AddToolDialog } from '@/components/tool/add-tool-dialog'

const mockRegisterMCP = vi.fn().mockResolvedValue({ tools: [] })
const mockCreateCustomTool = vi.fn().mockResolvedValue({})

vi.mock('@/lib/hooks/use-tools', () => ({
  useCreateCustomTool: () => ({
    mutateAsync: mockCreateCustomTool,
    isPending: false,
  }),
}))

vi.mock('@/lib/hooks/use-connections', () => ({
  useConnections: () => ({ data: [], isLoading: false }),
  useCreateConnection: () => ({
    mutateAsync: mockRegisterMCP,
    isPending: false,
  }),
  useDiscoverMcpTools: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useFindOrCreateCustomConnection: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
}))

vi.mock('@/lib/hooks/use-credentials', () => ({
  useCredentials: () => ({ data: [] }),
  useCredentialProviders: () => ({ data: [] }),
  useCreateCredential: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateCredential: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

function renderDialog() {
  return render(<AddToolDialog trigger={<button type="button">도구 추가</button>} />)
}

describe('AddToolDialog', () => {
  beforeEach(() => {
    mockRegisterMCP.mockClear()
    mockCreateCustomTool.mockClear()
  })

  it('opens dialog when trigger clicked', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    expect(
      screen.getByText('MCP 서버를 등록하거나 커스텀 도구를 직접 정의하세요.'),
    ).toBeInTheDocument()
  })

  it('shows MCP tab form fields by default', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    expect(screen.getByText('서버 이름')).toBeInTheDocument()
    expect(screen.getByText('서버 URL')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Google Workspace MCP')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('https://mcp.example.com')).toBeInTheDocument()
  })

  it('switches to custom tool tab', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    await user.click(screen.getByText('직접 정의'))
    expect(screen.getByPlaceholderText('날씨 조회')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('https://api.example.com/weather')).toBeInTheDocument()
  })

  it('MCP tab shows credential Select with "인증 없음" default', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    expect(screen.getAllByText('인증 없음').length).toBeGreaterThan(0)
  })

  it('shows custom tool HTTP method options', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    await user.click(screen.getByText('직접 정의'))
    expect(screen.getByText('GET')).toBeInTheDocument()
    expect(screen.getByText('POST')).toBeInTheDocument()
    expect(screen.getByText('PUT')).toBeInTheDocument()
  })

  it('register button is disabled when MCP fields are empty', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    const registerButton = screen.getByRole('button', { name: '등록하고 도구 탐색' })
    expect(registerButton).toBeDisabled()
  })

  it('shows custom tool parameter JSON schema field', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    await user.click(screen.getByText('직접 정의'))
    expect(screen.getByText('파라미터 (JSON Schema)')).toBeInTheDocument()
  })

  it('shows custom tool credential select with "인증 없음" default', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    await user.click(screen.getByText('직접 정의'))
    // MCP 탭과 동일하게 credential select 노출 — 기본값은 "인증 없음"
    expect(screen.getAllByText('인증 없음').length).toBeGreaterThan(0)
  })

  it('MCP submit button enables when name + url filled', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))

    await user.type(screen.getByPlaceholderText('Google Workspace MCP'), 'My MCP Server')
    await user.type(screen.getByPlaceholderText('https://mcp.example.com'), 'https://mcp.test.com')

    const registerButton = screen.getByRole('button', { name: '등록하고 도구 탐색' })
    expect(registerButton).not.toBeDisabled()
  })

  // M6 이후 CUSTOM 도구 등록은 credential 선택이 필수 — 인증 없음으로는 제출 불가.
  // useFindOrCreateCustomConnection + useCreateCustomTool 흐름은 통합 동작이라
  // 단위 테스트로 검증하기 어려움. e2e/smoke 또는 manual-e2e-e-m6-1.md 시나리오 1 참조.
  it.skip('submits custom tool form when filled (M6 credential 필수로 비활성)', () => {})
  it.skip('omits credential_id payload when 인증 없음 is selected (M6에서 credential 필수)', () => {})
})
