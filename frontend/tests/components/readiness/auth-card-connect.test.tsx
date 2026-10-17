import { AuthCardConnect } from '@/components/readiness/auth-card-connect'
import { render, screen } from '../../test-utils'

const readiness = vi.hoisted(() => ({ get: vi.fn(), notifyOwner: vi.fn(), summary: vi.fn() }))
vi.mock('@/lib/api/readiness', () => ({ readinessApi: readiness }))

const slack = {
  kind: 'mcp_server',
  id: 'srv-1',
  name: 'Slack',
  required: true,
  result: 'connect_by_me',
  credential: { mode: 'per_user', status: 'missing', definition_key: 'http_bearer' },
}

describe('AuthCardConnect (S22 "연결하기")', () => {
  beforeEach(() => vi.clearAllMocks())

  it('offers connect when the check can bind one of the asked connections', async () => {
    readiness.get.mockResolvedValue({ overall: 'needs_connection', items: [slack] })
    render(
      <AuthCardConnect
        agentId="a-1"
        resourceNames={['Slack']}
        disabled={false}
        onConnected={vi.fn()}
      />,
    )
    expect(await screen.findByRole('button', { name: '연결하기' })).toBeInTheDocument()
  })

  it('points to the credentials screen when nothing can be bound in place', async () => {
    const shared = {
      ...slack,
      credential: { mode: 'shared', status: 'expired', credential_id: 'c' },
    }
    readiness.get.mockResolvedValue({ overall: 'needs_connection', items: [shared] })
    render(
      <AuthCardConnect
        agentId="a-1"
        resourceNames={['Slack']}
        disabled={false}
        onConnected={vi.fn()}
      />,
    )
    expect(await screen.findByRole('link', { name: '자격증명 열기' })).toHaveAttribute(
      'href',
      '/settings/credentials',
    )
    expect(screen.queryByRole('button', { name: '연결하기' })).not.toBeInTheDocument()
  })
})
