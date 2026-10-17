import { ApiError } from '@/lib/api/client'
import { csrfStore } from '@/lib/auth/csrf'
import { ssoApi, tenantSsoApi } from '@/lib/api/sso'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('ssoApi', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    csrfStore.clear()
  })

  it('sends the LDAP login with the login CSRF header and without the session CSRF', async () => {
    csrfStore.set('session-csrf')
    fetchMock.mockResolvedValue(jsonResponse(200, { user: {}, csrf_token: 'new' }))
    await ssoApi.ldapLogin('c1', 'kim', 'pw', 'login-csrf')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toMatch(/\/api\/auth\/ldap\/c1\/login$/)
    expect(init.credentials).toBe('include')
    const headers = new Headers(init.headers)
    expect(headers.get('X-Login-CSRF')).toBe('login-csrf')
    expect(headers.get('X-CSRF-Token')).toBeNull()
    expect(JSON.parse(String(init.body))).toEqual({ username: 'kim', password: 'pw' })
  })

  it('surfaces the uniform 401 as an ApiError without a refresh retry', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(401, { error: { code: 'invalid_credentials', message: 'no' } }),
    )
    await expect(ssoApi.ldapLogin('c1', 'kim', 'bad', 't')).rejects.toMatchObject({
      status: 401,
      code: 'invalid_credentials',
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await expect(ssoApi.ldapLogin('c1', 'kim', 'bad', 't')).rejects.toBeInstanceOf(ApiError)
  })

  it('posts the email to discovery and the policy to the company route', async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { method: 'password', connections: [] }))
    await ssoApi.discover('a@corp.example')
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toEqual({
      email: 'a@corp.example',
    })
    fetchMock.mockResolvedValue(jsonResponse(200, { mode: 'required' }))
    await tenantSsoApi.putPolicy('t1', 'required', ['u1'])
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit]
    expect(url).toMatch(/\/api\/tenants\/t1\/sso-policy$/)
    expect(init.method).toBe('PUT')
    expect(JSON.parse(String(init.body))).toEqual({
      mode: 'required',
      break_glass_user_ids: ['u1'],
    })
  })
})
