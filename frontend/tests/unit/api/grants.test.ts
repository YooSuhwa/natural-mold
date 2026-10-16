import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }))

vi.mock('@/lib/api/client', () => ({ apiFetch }))

import { grantsApi } from '@/lib/api/grants'
import { parseApiErrorBody, throwApiError } from '@/lib/api/errors'
import { ApiError } from '@/lib/api/errors'

describe('grantsApi', () => {
  beforeEach(() => apiFetch.mockReset())

  it('targets the per-type E-01 routes', async () => {
    apiFetch.mockResolvedValue({})
    await grantsApi.access('mcp_server', 'id/1')
    await grantsApi.list('skill', 's1')
    await grantsApi.create('agent', 'a1', {
      principal: { type: 'group', id: 'g1' },
      level: 'viewer',
    })
    await grantsApi.transfer('tool', 't1', { new_owner_id: 'u2', keep_manager: true })
    await grantsApi.revoke('grant-1')
    expect(apiFetch.mock.calls).toEqual([
      ['/api/resources/mcp_server/id%2F1/access'],
      ['/api/resources/skill/s1/grants'],
      [
        '/api/resources/agent/a1/grants',
        {
          method: 'POST',
          body: JSON.stringify({ principal: { type: 'group', id: 'g1' }, level: 'viewer' }),
        },
      ],
      [
        '/api/resources/tool/t1/transfer',
        { method: 'POST', body: JSON.stringify({ new_owner_id: 'u2', keep_manager: true }) },
      ],
      ['/api/grants/grant-1', { method: 'DELETE' }],
    ])
  })
})

describe('AppError details', () => {
  function response(body: unknown, status = 409): Response {
    return new Response(JSON.stringify(body), { status })
  }

  it('keeps error.details on the thrown ApiError', async () => {
    const details = { delegations: [{ resource_type: 'credential', resource_id: 'c1', name: 'X' }] }
    const error = await throwApiError(
      response({ error: { code: 'confirm_required', message: 'm', details } }),
      'UNKNOWN',
    ).catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('confirm_required')
    expect((error as ApiError).details).toEqual(details)
  })

  it('reports null details for bodies without an object', async () => {
    expect(
      (await parseApiErrorBody(response({ error: { code: 'x', details: [1] } }))).details,
    ).toBe(null)
    expect((await parseApiErrorBody(response({ detail: 'nope' }))).details).toBe(null)
  })
})
