import { render, screen, userEvent } from '../../test-utils'
import { AddToolDialog } from '@/components/tool/add-tool-dialog'

const mockRegisterMCP = vi.fn().mockResolvedValue({})
const mockCreateCustomTool = vi.fn().mockResolvedValue({})

vi.mock('@/lib/hooks/use-tools', () => ({
  useRegisterMCPServer: () => ({
    mutateAsync: mockRegisterMCP,
    isPending: false,
  }),
  useCreateCustomTool: () => ({
    mutateAsync: mockCreateCustomTool,
    isPending: false,
  }),
}))

function renderDialog() {
  return render(<AddToolDialog trigger={<button type="button">도구 추가</button>} />)
}

describe('AddToolDialog', () => {
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

  it('shows MCP auth options', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    expect(screen.getByText('없음')).toBeInTheDocument()
    expect(screen.getByText('API Key')).toBeInTheDocument()
    expect(screen.getByText('OAuth')).toBeInTheDocument()
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
    const registerButton = screen.getByRole('button', { name: '등록' })
    expect(registerButton).toBeDisabled()
  })

  it('shows api key field when auth is api_key in MCP tab', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    // Click API Key radio
    const apiKeyRadio = screen.getByDisplayValue('api_key')
    await user.click(apiKeyRadio)
    expect(screen.getByPlaceholderText('sk-xxxxxxxxxxxx')).toBeInTheDocument()
  })

  it('shows custom tool parameter JSON schema field', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    await user.click(screen.getByText('직접 정의'))
    expect(screen.getByText('파라미터 (JSON Schema)')).toBeInTheDocument()
  })

  it('shows custom tool auth options', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    await user.click(screen.getByText('직접 정의'))
    // Custom auth types: 없음, API Key, Bearer
    expect(screen.getByText('Bearer')).toBeInTheDocument()
  })

  it('submits MCP server form when filled and clicked', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))

    // Fill in MCP form
    await user.type(screen.getByPlaceholderText('Google Workspace MCP'), 'My MCP Server')
    await user.type(screen.getByPlaceholderText('https://mcp.example.com'), 'https://mcp.test.com')

    const registerButton = screen.getByRole('button', { name: '등록' })
    expect(registerButton).not.toBeDisabled()
    await user.click(registerButton)

    expect(mockRegisterMCP).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'My MCP Server',
        url: 'https://mcp.test.com',
      }),
    )
  })

  it('submits custom tool form when filled', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    await user.click(screen.getByText('직접 정의'))

    // Fill in custom tool form
    await user.type(screen.getByPlaceholderText('날씨 조회'), 'Weather API')
    await user.type(
      screen.getByPlaceholderText('https://api.example.com/weather'),
      'https://api.weather.com',
    )

    const registerButton = screen.getByRole('button', { name: '등록' })
    expect(registerButton).not.toBeDisabled()
    await user.click(registerButton)

    expect(mockCreateCustomTool).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Weather API',
        api_url: 'https://api.weather.com',
      }),
    )
  })

  it('shows custom auth api key field when auth type selected', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('도구 추가'))
    await user.click(screen.getByText('직접 정의'))

    // Select bearer auth
    const bearerRadio = screen.getByDisplayValue('bearer')
    await user.click(bearerRadio)
    // API key input should appear
    expect(screen.getByPlaceholderText('sk-xxxxxxxxxxxx')).toBeInTheDocument()
  })
})
