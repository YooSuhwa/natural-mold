import { CredentialModeControl } from '@/components/readiness/credential-mode-control'
import { ApiError } from '@/lib/api/client'
import { render, screen, userEvent, waitFor } from '../../test-utils'

const bindings = vi.hoisted(() => ({
  bind: vi.fn(),
  list: vi.fn(),
  unbind: vi.fn(),
  setMode: vi.fn(),
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('@/lib/api/credential-bindings', () => ({ credentialBindingsApi: bindings }))
vi.mock('sonner', () => ({ toast }))

const base = {
  resourceType: 'tool' as const,
  resourceId: 'tool-1',
  credentialId: null,
  definitionKey: null,
}

async function choose(name: string) {
  await userEvent.click(screen.getByTestId('credential-mode-select'))
  await userEvent.click(await screen.findByRole('option', { name }))
}

describe('CredentialModeControl (F-04)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    bindings.setMode.mockResolvedValue({})
  })

  it('switches a single-type tool to per-user connections with its type', async () => {
    render(<CredentialModeControl {...base} mode="none" acceptedKeys={['http_bearer']} />)
    expect(screen.getByTestId('credential-mode-select')).toHaveTextContent(
      '이 리소스의 계정을 함께 사용',
    )
    await choose('사용자마다 자기 계정 연결')
    await waitFor(() =>
      expect(bindings.setMode).toHaveBeenCalledWith('tool', 'tool-1', 'per_user', 'http_bearer'),
    )
    expect(toast.success).toHaveBeenCalledWith('이제 사용자마다 자기 계정을 연결합니다')
  })

  it('asks for the type first when several types are accepted and none is recorded', async () => {
    render(
      <CredentialModeControl
        {...base}
        resourceType="mcp_server"
        resourceId="srv-1"
        mode="none"
        acceptedKeys={['http_bearer', 'mcp_oauth2']}
      />,
    )
    await choose('사용자마다 자기 계정 연결')
    expect(bindings.setMode).not.toHaveBeenCalled()
    await userEvent.click(screen.getByTestId('credential-mode-type-select'))
    await userEvent.click(await screen.findByRole('option', { name: 'mcp_oauth2' }))
    await waitFor(() =>
      expect(bindings.setMode).toHaveBeenCalledWith(
        'mcp_server',
        'srv-1',
        'per_user',
        'mcp_oauth2',
      ),
    )
  })

  it.each([
    ['cred-1', 'shared'],
    [null, 'none'],
  ] as const)(
    'leaves per-user connections for the resource account (%s → %s)',
    async (cred, mode) => {
      render(
        <CredentialModeControl
          {...base}
          credentialId={cred}
          definitionKey="http_bearer"
          mode="per_user"
          acceptedKeys={['http_bearer']}
        />,
      )
      await choose('이 리소스의 계정을 함께 사용')
      await waitFor(() =>
        expect(bindings.setMode).toHaveBeenCalledWith('tool', 'tool-1', mode, null),
      )
    },
  )

  it('explains a blocked reshare instead of failing silently', async () => {
    bindings.setMode.mockRejectedValue(new ApiError(403, 'credential_reshare_blocked', 'forbidden'))
    render(
      <CredentialModeControl
        {...base}
        credentialId="cred-1"
        definitionKey="http_bearer"
        mode="per_user"
        acceptedKeys={['http_bearer']}
      />,
    )
    await choose('이 리소스의 계정을 함께 사용')
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('이 계정을 다른 사람과 함께 쓸 권한이 없습니다'),
    )
  })
})
