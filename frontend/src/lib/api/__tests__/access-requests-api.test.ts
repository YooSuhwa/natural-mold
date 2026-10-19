import { afterEach, describe, expect, it, vi } from 'vitest'

import { accessRequestsApi } from '../access-requests'

function respond(status: number, body: string) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body || null, { status })))
}

describe('accessRequestsApi', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('reads the body-less 202 as undefined (nothing revealed, plan 8.4)', async () => {
    respond(202, '')
    const body = { resource_type: 'agent', resource_id: 'a-1', level: 'editor' } as const
    await expect(accessRequestsApi.create(body)).resolves.toBeUndefined()
  })

  it('returns the request view of a created request', async () => {
    respond(201, JSON.stringify({ id: 'req-1', status: 'pending' }))
    const body = { resource_type: 'agent', resource_id: 'a-1', level: 'editor' } as const
    await expect(accessRequestsApi.create(body)).resolves.toMatchObject({ id: 'req-1' })
  })

  it('lists a box with its cursor', async () => {
    respond(200, JSON.stringify({ items: [], next_cursor: null, has_more: false }))
    await accessRequestsApi.list('handled', 'c-2')
    const [url] = vi.mocked(fetch).mock.calls[0]
    expect(String(url)).toContain('/api/access-requests?box=handled&cursor=c-2')
  })
})
