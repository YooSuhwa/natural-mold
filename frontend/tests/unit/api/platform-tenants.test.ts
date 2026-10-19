import { platformTenantsApi, tenantVersionHeader } from '@/lib/api/platform-tenants'

const apiFetch = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api/client', () => ({ apiFetch }))

describe('tenantVersionHeader', () => {
  it('echoes the backend isoformat ETag (+00:00) for a JSON `Z` timestamp', () => {
    expect(tenantVersionHeader('2026-10-17T03:00:00.123456Z')).toEqual({
      'If-Match': '"2026-10-17T03:00:00.123456+00:00"',
    })
    expect(tenantVersionHeader('2026-10-17T03:00:00+00:00')).toEqual({
      'If-Match': '"2026-10-17T03:00:00+00:00"',
    })
  })
})

describe('platformTenantsApi.scheduleDeletion', () => {
  it('sends the typed slug as confirm_slug with the version header', async () => {
    apiFetch.mockResolvedValue({})
    await platformTenantsApi.scheduleDeletion('t-1', 'typed-slug', '2026-10-17T03:00:00Z')
    expect(apiFetch).toHaveBeenCalledWith('/api/admin/tenants/t-1/schedule-deletion', {
      method: 'POST',
      headers: { 'If-Match': '"2026-10-17T03:00:00+00:00"' },
      body: JSON.stringify({ confirm_slug: 'typed-slug' }),
    })
  })
})
