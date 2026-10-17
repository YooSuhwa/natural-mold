import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { credentialsApi } from '@/lib/api/credentials'
import { useOAuthAuthorize, useOAuthCompletion } from '../use-oauth-completion'

vi.mock('@/lib/api/credentials', () => ({
  credentialsApi: { startOAuth2: vi.fn() },
}))

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

function post(data: unknown, origin = window.location.origin) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data, origin }))
  })
}

const completed = (credentialId?: string) => ({ type: 'moldy.oauth.completed', credentialId })

describe('useOAuthCompletion', () => {
  let queryClient: QueryClient
  beforeEach(() => {
    queryClient = new QueryClient()
    vi.clearAllMocks()
  })

  it('reports the completion of its credential and refreshes credential and readiness data', () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const onCompleted = vi.fn()
    renderHook(() => useOAuthCompletion({ credentialId: 'cred-1', onCompleted }), {
      wrapper: wrapper(queryClient),
    })

    post(completed('cred-1'))

    expect(onCompleted).toHaveBeenCalledWith('cred-1')
    const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey)
    expect(keys).toEqual(
      expect.arrayContaining([['credentials'], ['credentials', 'cred-1'], ['readiness']]),
    )
  })

  it('ignores other origins, other credentials and unrelated messages', () => {
    const onCompleted = vi.fn()
    renderHook(() => useOAuthCompletion({ credentialId: 'cred-1', onCompleted }), {
      wrapper: wrapper(queryClient),
    })

    post(completed('cred-1'), 'https://evil.example')
    post(completed('cred-2'))
    post({ type: 'something.else', credentialId: 'cred-1' })

    expect(onCompleted).not.toHaveBeenCalled()
  })

  it('accepts a message without a credential id for the pending flow', () => {
    const onCompleted = vi.fn()
    renderHook(() => useOAuthCompletion({ credentialId: 'cred-1', onCompleted }), {
      wrapper: wrapper(queryClient),
    })

    post(completed())

    expect(onCompleted).toHaveBeenCalledWith('cred-1')
  })

  it('does not listen while disabled', () => {
    const onCompleted = vi.fn()
    renderHook(() => useOAuthCompletion({ credentialId: 'cred-1', enabled: false, onCompleted }), {
      wrapper: wrapper(queryClient),
    })

    post(completed('cred-1'))

    expect(onCompleted).not.toHaveBeenCalled()
  })
})

describe('useOAuthAuthorize', () => {
  beforeEach(() => vi.clearAllMocks())

  it('opens the popup first, then sends it to the authorization URL', async () => {
    const popup = { location: { href: 'about:blank' }, close: vi.fn(), focus: vi.fn() }
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window)
    vi.mocked(credentialsApi.startOAuth2).mockResolvedValue({
      authorization_url: 'https://auth.example/authorize?x=1',
      state: 's',
    })
    const { result } = renderHook(() => useOAuthAuthorize(), {
      wrapper: wrapper(new QueryClient()),
    })

    let outcome: string | undefined
    await act(async () => {
      outcome = await result.current.authorize('cred-1')
    })

    expect(outcome).toBe('started')
    expect(open).toHaveBeenCalledWith('about:blank', 'moldy-oauth', expect.any(String))
    expect(credentialsApi.startOAuth2).toHaveBeenCalledWith('cred-1')
    expect(popup.location.href).toBe('https://auth.example/authorize?x=1')
    open.mockRestore()
  })

  it('reports a blocked popup without starting the flow', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    const { result } = renderHook(() => useOAuthAuthorize(), {
      wrapper: wrapper(new QueryClient()),
    })

    let outcome: string | undefined
    await act(async () => {
      outcome = await result.current.authorize('cred-1')
    })

    expect(outcome).toBe('blocked')
    expect(credentialsApi.startOAuth2).not.toHaveBeenCalled()
    open.mockRestore()
  })

  it('closes the popup when the start request fails', async () => {
    const popup = { location: { href: 'about:blank' }, close: vi.fn(), focus: vi.fn() }
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window)
    vi.mocked(credentialsApi.startOAuth2).mockRejectedValue(new Error('nope'))
    const { result } = renderHook(() => useOAuthAuthorize(), {
      wrapper: wrapper(new QueryClient()),
    })

    let outcome: string | undefined
    await act(async () => {
      outcome = await result.current.authorize('cred-1')
    })

    expect(outcome).toBe('failed')
    expect(popup.close).toHaveBeenCalled()
    open.mockRestore()
  })
})
