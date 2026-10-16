import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/lib/api/errors'
import { meOrganizationsApi, orgAdminApi, tenantOrganizationsApi } from '@/lib/api/organizations'
import {
  isStaleVersionError,
  useActiveOrganization,
  useAddOrgMember,
  useRenameTenantOrganization,
  useSwitchActiveOrganization,
} from '@/lib/hooks/use-organizations'
import { organizationQueryKeys } from '@/lib/query-keys/organizations'
import type { OrganizationSummary } from '@/lib/types/organization'
import { makeOrganization } from './organization-fixtures'

vi.mock('@/lib/api/organizations', () => ({
  meOrganizationsApi: { list: vi.fn(), setActive: vi.fn() },
  tenantOrganizationsApi: { rename: vi.fn() },
  orgAdminApi: { addMember: vi.fn() },
}))

const ALPHA = makeOrganization()
const BETA = makeOrganization({ id: 'org-2', name: 'Beta', is_default: false, is_active: false })

function setup() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return { queryClient, Wrapper }
}

describe('organization hooks', () => {
  beforeEach(() => vi.clearAllMocks())

  it('exposes the server-flagged active organization', async () => {
    vi.mocked(meOrganizationsApi.list).mockResolvedValue([
      { ...ALPHA, is_active: false },
      { ...BETA, is_active: true },
    ])
    const { Wrapper } = setup()
    const { result } = renderHook(() => useActiveOrganization(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.organizations).toHaveLength(2))
    expect(result.current.activeOrganization?.id).toBe('org-2')
  })

  it('has no active organization when the server flags none', async () => {
    vi.mocked(meOrganizationsApi.list).mockResolvedValue([{ ...ALPHA, is_active: false }])
    const { Wrapper } = setup()
    const { result } = renderHook(() => useActiveOrganization(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.organizations).toHaveLength(1))
    expect(result.current.activeOrganization).toBeNull()
  })

  it('switching flips is_active and resets org-scoped queries only', async () => {
    vi.mocked(meOrganizationsApi.setActive).mockResolvedValue({
      org_id: 'org-2',
      tenant_id: 'tenant-1',
    })
    vi.mocked(meOrganizationsApi.list).mockImplementation(
      () => new Promise<OrganizationSummary[]>(() => undefined),
    )
    const { Wrapper, queryClient } = setup()
    queryClient.setQueryData(['agents'], ['agent-from-org-1'])
    queryClient.setQueryData(['auth', 'session'], { id: 'user-1' })
    queryClient.setQueryData(organizationQueryKeys.mine, [ALPHA, BETA])

    const { result } = renderHook(() => useSwitchActiveOrganization(), { wrapper: Wrapper })
    await act(() => result.current.mutateAsync('org-2'))

    expect(meOrganizationsApi.setActive).toHaveBeenCalledWith('org-2')
    expect(queryClient.getQueryData(['agents'])).toBeUndefined()
    expect(queryClient.getQueryData(['auth', 'session'])).toEqual({ id: 'user-1' })
    const mine = queryClient.getQueryData<OrganizationSummary[]>(organizationQueryKeys.mine)
    expect(mine?.map((org) => [org.id, org.is_active])).toEqual([
      ['org-1', false],
      ['org-2', true],
    ])
  })

  it('a failed switch keeps caches', async () => {
    vi.mocked(meOrganizationsApi.setActive).mockRejectedValue(new Error('forbidden'))
    const { Wrapper, queryClient } = setup()
    queryClient.setQueryData(['agents'], ['kept'])
    const { result } = renderHook(() => useSwitchActiveOrganization(), { wrapper: Wrapper })
    await act(async () => {
      await expect(result.current.mutateAsync('org-2')).rejects.toThrow('forbidden')
    })
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

  it('org writes refetch the org subtree even when the version is stale', async () => {
    const stale = new ApiError(409, 'stale_version', 'stale')
    vi.mocked(orgAdminApi.addMember).mockRejectedValue(stale)
    const { Wrapper, queryClient } = setup()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(() => useAddOrgMember('org-1'), { wrapper: Wrapper })
    await act(async () => {
      await expect(result.current.mutateAsync({ userId: 'user-9', version: 'v1' })).rejects.toBe(
        stale,
      )
    })
    expect(orgAdminApi.addMember).toHaveBeenCalledWith('org-1', 'user-9', 'v1')
    expect(isStaleVersionError(stale)).toBe(true)
    expect(isStaleVersionError(new Error('x'))).toBe(false)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: organizationQueryKeys.orgAll('org-1') })
  })
})
