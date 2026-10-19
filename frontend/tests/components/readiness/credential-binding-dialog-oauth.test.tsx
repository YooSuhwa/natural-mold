import { act } from '@testing-library/react'

import { CredentialBindingDialog } from '@/components/readiness/credential-binding-dialog'
import { render, screen, userEvent, waitFor } from '../../test-utils'

const bindings = vi.hoisted(() => ({ bind: vi.fn(), list: vi.fn(), unbind: vi.fn() }))
const credentials = vi.hoisted(() => ({
  list: vi.fn(),
  listTypes: vi.fn(),
  create: vi.fn(),
  startOAuth2: vi.fn(),
}))
vi.mock('@/lib/api/credential-bindings', () => ({ credentialBindingsApi: bindings }))
vi.mock('@/lib/api/credentials', () => ({ credentialsApi: credentials, systemCredentialsApi: {} }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const target = {
  resourceType: 'mcp_server' as const,
  resourceId: 'srv-1',
  resourceName: 'Slack',
  definitionKey: 'mcp_oauth2',
}

const oauthType = {
  key: 'mcp_oauth2',
  display_name: 'MCP OAuth',
  category: 'oauth',
  extends: [],
  properties: [],
  has_test: false,
  has_oauth: true,
}

function credential(id: string, status: string) {
  return { id, name: `Slack ${id}`, definition_key: 'mcp_oauth2', status, is_system: false }
}

function completeOAuth(credentialId: string) {
  act(() => {
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'moldy.oauth.completed', credentialId },
        origin: window.location.origin,
      }),
    )
  })
}

describe('CredentialBindingDialog OAuth (G-05)', () => {
  let open: ReturnType<typeof vi.spyOn>
  let popup: { location: { href: string }; close: ReturnType<typeof vi.fn>; focus: () => void }

  beforeEach(() => {
    vi.clearAllMocks()
    popup = { location: { href: 'about:blank' }, close: vi.fn(), focus: vi.fn() }
    open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window)
    credentials.listTypes.mockResolvedValue([oauthType])
    credentials.startOAuth2.mockResolvedValue({
      authorization_url: 'https://auth.example/authorize',
      state: 's',
    })
    bindings.bind.mockResolvedValue({ id: 'b-1' })
  })

  afterEach(() => open.mockRestore())

  it('authorizes an expired OAuth credential after binding it and waits for the popup', async () => {
    credentials.list.mockResolvedValue([credential('c-1', 'expired')])
    const onConnected = vi.fn()
    render(
      <CredentialBindingDialog
        open
        onOpenChange={vi.fn()}
        target={target}
        onConnected={onConnected}
      />,
    )

    await userEvent.click(await screen.findByRole('button', { name: 'Slack c-1 연결' }))

    await waitFor(() => expect(credentials.startOAuth2).toHaveBeenCalledWith('c-1'))
    expect(bindings.bind).toHaveBeenCalledWith('mcp_server', 'srv-1', 'c-1')
    expect(popup.location.href).toBe('https://auth.example/authorize')
    expect(await screen.findByRole('status')).toBeInTheDocument()
    expect(onConnected).not.toHaveBeenCalled()

    completeOAuth('c-1')
    await waitFor(() => expect(onConnected).toHaveBeenCalledTimes(1))
  })

  it('binds an active credential without OAuth', async () => {
    credentials.list.mockResolvedValue([credential('c-2', 'active')])
    const onConnected = vi.fn()
    render(
      <CredentialBindingDialog
        open
        onOpenChange={vi.fn()}
        target={target}
        onConnected={onConnected}
      />,
    )

    await userEvent.click(await screen.findByRole('button', { name: 'Slack c-2 연결' }))

    await waitFor(() => expect(onConnected).toHaveBeenCalledTimes(1))
    expect(credentials.startOAuth2).not.toHaveBeenCalled()
  })

  it('creates a new OAuth credential, authorizes it and connects once the popup completes', async () => {
    credentials.list.mockResolvedValue([])
    credentials.create.mockResolvedValue(credential('c-new', 'auth_needed'))
    const onConnected = vi.fn()
    render(
      <CredentialBindingDialog
        open
        onOpenChange={vi.fn()}
        target={target}
        onConnected={onConnected}
      />,
    )

    await userEvent.click(await screen.findByRole('button', { name: /새로 연결/ }))
    await userEvent.click(await screen.findByRole('button', { name: '자격증명 저장' }))

    await waitFor(() => expect(credentials.startOAuth2).toHaveBeenCalledWith('c-new'))
    await waitFor(() => expect(bindings.bind).toHaveBeenCalledWith('mcp_server', 'srv-1', 'c-new'))
    expect(onConnected).not.toHaveBeenCalled()

    completeOAuth('c-new')
    await waitFor(() => expect(onConnected).toHaveBeenCalledTimes(1))
  })

  it('connects when the popup completes before the binding answers', async () => {
    credentials.list.mockResolvedValue([])
    credentials.create.mockResolvedValue(credential('c-fast', 'auth_needed'))
    let resolveBind: (value: unknown) => void = () => {}
    bindings.bind.mockReturnValue(new Promise((resolve) => (resolveBind = resolve)))
    const onConnected = vi.fn()
    render(
      <CredentialBindingDialog
        open
        onOpenChange={vi.fn()}
        target={target}
        onConnected={onConnected}
      />,
    )

    await userEvent.click(await screen.findByRole('button', { name: /새로 연결/ }))
    await userEvent.click(await screen.findByRole('button', { name: '자격증명 저장' }))
    await waitFor(() => expect(bindings.bind).toHaveBeenCalled())
    completeOAuth('c-fast')
    expect(onConnected).not.toHaveBeenCalled()

    act(() => resolveBind({ id: 'b-1' }))
    await waitFor(() => expect(onConnected).toHaveBeenCalledTimes(1))
  })

  it('closes the popup and stops waiting when the binding fails', async () => {
    credentials.list.mockResolvedValue([])
    credentials.create.mockResolvedValue(credential('c-bad', 'auth_needed'))
    bindings.bind.mockRejectedValue(new Error('nope'))
    const onConnected = vi.fn()
    render(
      <CredentialBindingDialog
        open
        onOpenChange={vi.fn()}
        target={target}
        onConnected={onConnected}
      />,
    )

    await userEvent.click(await screen.findByRole('button', { name: /새로 연결/ }))
    await userEvent.click(await screen.findByRole('button', { name: '자격증명 저장' }))

    await waitFor(() => expect(popup.close).toHaveBeenCalled())
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    completeOAuth('c-bad')
    expect(onConnected).not.toHaveBeenCalled()
  })
})
