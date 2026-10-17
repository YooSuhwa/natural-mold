import { render, screen, userEvent, waitFor } from '../../test-utils'
import { SsoLoginOptions } from '@/components/auth/SsoLoginOptions'
import { mapAuthError } from '@/components/auth/auth-errors'
import { ApiError } from '@/lib/api/client'
import type { SsoDiscovery } from '@/lib/types/sso'

const SSO: SsoDiscovery = {
  method: 'sso',
  sso_required: true,
  login_csrf: 'csrf',
  connections: [
    { id: 'c-oidc', protocol: 'oidc', name: '가나전자', start_url: 'http://api/start' },
    { id: 'c-ldap', protocol: 'ldap', name: '가나전자', start_url: null },
  ],
}

describe('SsoLoginOptions (email-first login, plan 10.10)', () => {
  it('renders nothing when the email keeps the password login', () => {
    const { container } = render(
      <SsoLoginOptions
        discovery={{ ...SSO, method: 'password', connections: [] }}
        onLdapLogin={vi.fn()}
        ldapPending={false}
        ldapError={false}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('offers the company IdP, the SSO-required notice and an LDAP account form', async () => {
    const onLdapLogin = vi.fn().mockResolvedValue(undefined)
    render(
      <SsoLoginOptions
        discovery={SSO}
        onLdapLogin={onLdapLogin}
        ldapPending={false}
        ldapError={false}
      />,
    )
    expect(screen.getByText('이 회사는 회사 계정(SSO) 로그인이 필수입니다.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '가나전자 계정으로 계속' })).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('아이디'), 'kim')
    await userEvent.type(screen.getByLabelText('비밀번호'), 'pw')
    await userEvent.click(screen.getByRole('button', { name: '회사 계정으로 로그인' }))
    await waitFor(() => expect(onLdapLogin).toHaveBeenCalledWith(SSO.connections[1], 'kim', 'pw'))
  })

  it('shows one uniform error for a failed LDAP login', () => {
    render(<SsoLoginOptions discovery={SSO} onLdapLogin={vi.fn()} ldapPending={false} ldapError />)
    expect(screen.getByText('아이디 또는 비밀번호가 올바르지 않습니다.')).toBeInTheDocument()
  })

  it('maps the SSO-required password refusal to its own message', () => {
    const error = new ApiError(403, 'sso_required', 'x')
    expect(mapAuthError(error, 'login').messageKey).toBe('auth.errors.ssoRequired')
  })

  it('sends a company-domain signup to SSO', () => {
    const error = new ApiError(403, 'sso_signup_blocked', 'x')
    expect(mapAuthError(error, 'register')).toEqual({
      field: 'email',
      messageKey: 'auth.errors.ssoSignupBlocked',
    })
  })
})
