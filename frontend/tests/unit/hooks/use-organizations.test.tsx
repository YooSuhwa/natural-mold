import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider as JotaiProvider, createStore } from 'jotai'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { meOrganizationsApi, tenantOrganizationsApi } from '@/lib/api/organizations'
import {
  fallbackActiveOrganization,
  resolveActiveOrganization,
  useActiveOrganization,
  useRenameTenantOrganization,
  useSwitchActiveOrganization,
} from '@/lib/hooks/use-organizations'
import { organizationQueryKeys } from '@/lib/query-keys/organizations'
import { activeOrganizationIdAtom } from '@/lib/stores/organization-store'
import { makeOrganization } from './organization-fixtures'

vi.mock('@/lib/api/organizations', () => ({
  meOrganizationsApi: { list: vi.fn(), setActive: vi.fn() },
  tenantOrganizationsApi: { rename: vi.fn() },
}))

const ALPHA = makeOrganization()
const BETA = makeOrganization({ id: 'org-2', name: 'Beta', is_default: false })

function setup() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const store = createStore()
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <JotaiProvider store={store}>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </JotaiProvider>
    )
  }
  return { queryClient, store, Wrapper }
}

describe('active organization resolution', () => {
  it('mirrors the backend fallback order and skips unavailable companies', () => {
    const suspended = makeOrganization({
      id: 'org-0',
      tenant_name: 'Aaa',
      tenant_status: 'suspended',
    })
    expect(fallbackActiveOrganization([BETA, suspended, ALPHA])?.id).toBe('org-1')
    expect(fallbackActiveOrganization([])).toBeNull()
  })

  it('prefers the remembered selection only while it is still a membership', () => {
    expect(resolveActiveOrganization([ALPHA, BETA], 'org-2')?.id).toBe('org-2')
    expect(resolveActiveOrganization([ALPHA, BETA], 'gone')?.id).toBe('org-1')
  })
})

describe('organization hooks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
  })

  it('loads my organizations and exposes the active one', async () => {
    vi.mocked(meOrganizationsApi.list).mockResolvedValue([ALPHA, BETA])
    const { Wrapper } = setup()
    const { result } = renderHook(() => useActiveOrganization(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.organizations).toHaveLength(2))
    expect(result.current.activeOrganization?.id).toBe('org-1')
  })

  it('switching remembers the org and resets org-scoped queries only', async () => {
    vi.mocked(meOrganizationsApi.setActive).mockResolvedValue({
      org_id: 'org-2',
      tenant_id: 'tenant-1',
    })
    const { Wrapper, queryClient, store } = setup()
    queryClient.setQueryData(['agents'], ['agent-from-org-1'])
    queryClient.setQueryData(['auth', 'session'], { id: 'user-1' })
    queryClient.setQueryData(organizationQueryKeys.mine, [ALPHA, BETA])

    const { result } = renderHook(() => useSwitchActiveOrganization(), { wrapper: Wrapper })
    await act(() => result.current.mutateAsync('org-2'))

    expect(meOrganizationsApi.setActive).toHaveBeenCalledWith('org-2')
    expect(store.get(activeOrganizationIdAtom)).toBe('org-2')
    expect(queryClient.getQueryData(['agents'])).toBeUndefined()
    expect(queryClient.getQueryData(['auth', 'session'])).toEqual({ id: 'user-1' })
    expect(queryClient.getQueryData(organizationQueryKeys.mine)).toEqual([ALPHA, BETA])
  })

  it('a failed switch keeps the previous selection and caches', async () => {
    vi.mocked(meOrganizationsApi.setActive).mockRejectedValue(new Error('forbidden'))
    const { Wrapper, queryClient, store } = setup()
    store.set(activeOrganizationIdAtom, 'org-1')
    queryClient.setQueryData(['agents'], ['kept'])

    const { result } = renderHook(() => useSwitchActiveOrganization(), { wrapper: Wrapper })
    await act(async () => {
      await expect(result.current.mutateAsync('org-2')).rejects.toThrow('forbidden')
    })

    expect(store.get(activeOrganizationIdAtom)).toBe('org-1')
    expect(queryClient.getQueryData(['agents'])).toEqual(['kept'])
  })

  it('rename passes the version and invalidates the tenant directory', async () => {
    vi.mocked(tenantOrganizationsApi.rename).mockResolvedValue({
      id: 'org-1',
      name: 'New',
      slug: 'alpha',
      status: 'active',
      is_default: true,
      default_member_role: 'member',
      member_count: 3,
      administrator_user_id: null,
      updated_at: '2026-10-16T00:00:01Z',
    })
    const { Wrapper, queryClient } = setup()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(() => useRenameTenantOrganization('tenant-1'), {
      wrapper: Wrapper,
    })
    await act(() =>
      result.current.mutateAsync({
        orgId: 'org-1',
        data: { name: 'New' },
        updatedAt: '2026-10-16T00:00:00Z',
      }),
    )
    expect(tenantOrganizationsApi.rename).toHaveBeenCalledWith(
      'tenant-1',
      'org-1',
      { name: 'New' },
      '2026-10-16T00:00:00Z',
    )
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: organizationQueryKeys.tenantAll('tenant-1'),
    })
  })
})
