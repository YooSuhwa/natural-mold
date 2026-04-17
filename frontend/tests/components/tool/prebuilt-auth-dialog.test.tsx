import { render, screen, userEvent } from '../../test-utils'
import { PrebuiltAuthDialog } from '@/components/tool/prebuilt-auth-dialog'
import type { Tool } from '@/lib/types'

const mockMutate = vi.fn()

vi.mock('@/lib/hooks/use-tools', () => ({
  useUpdateToolAuthConfig: () => ({
    mutate: mockMutate,
    isPending: false,
  }),
}))

vi.mock('@/lib/hooks/use-credentials', () => ({
  useCredentials: () => ({ data: [] }),
  useCredentialProviders: () => ({ data: [] }),
  useCreateCredential: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateCredential: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

const naverTool: Tool = {
  id: 'tool-naver',
  type: 'prebuilt',
  is_system: true,
  mcp_server_id: null,
  name: 'Naver 검색',
  description: '네이버 검색 API',
  parameters_schema: null,
  api_url: null,
  http_method: null,
  auth_type: null,
  auth_config: null,
  credential_id: null,
  tags: null,
  agent_count: 0,
  created_at: '2026-01-01T00:00:00Z',
}

function renderDialog(tool: Tool = naverTool) {
  return render(<PrebuiltAuthDialog tool={tool} trigger={<button type="button">키 설정</button>} />)
}

describe('PrebuiltAuthDialog', () => {
  beforeEach(() => {
    mockMutate.mockClear()
  })

  it('opens dialog with tool info when trigger clicked', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('키 설정'))
    expect(screen.getByText('Naver 검색 API 키 설정')).toBeInTheDocument()
  })

  it('shows credential Select with "인증 없음" option', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('키 설정'))
    // Both the Select trigger and the option list contain "인증 없음"
    expect(screen.getAllByText('인증 없음').length).toBeGreaterThan(0)
  })

  it('has save and cancel buttons', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('키 설정'))
    expect(screen.getByText('저장')).toBeInTheDocument()
    expect(screen.getByText('취소')).toBeInTheDocument()
  })

  it('calls mutate when save is clicked with no credential selected', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText('키 설정'))
    await user.click(screen.getByText('저장'))
    expect(mockMutate).toHaveBeenCalledWith(
      { id: 'tool-naver', authConfig: {}, credentialId: null },
      expect.any(Object),
    )
  })

  it('calls mutate with credentialId when tool already has a credential bound', async () => {
    const user = userEvent.setup()
    const bound: Tool = { ...naverTool, credential_id: 'cred-123' }
    renderDialog(bound)
    await user.click(screen.getByText('키 설정'))
    await user.click(screen.getByText('저장'))
    expect(mockMutate).toHaveBeenCalledWith(
      { id: 'tool-naver', authConfig: {}, credentialId: 'cred-123' },
      expect.any(Object),
    )
  })
})
