import { afterEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../setup'
import { apiFetch, apiFetchBinary, ApiError, resetRefreshBackoff } from '@/lib/api/client'
import { beginOrgSwitch, finishOrgSwitch } from '@/lib/org-scope/org-scope-signal'

const API_BASE = 'http://localhost:8001'

afterEach(() => {
  resetRefreshBackoff()
})

describe('apiFetchBinary', () => {
  it('retries once after a successful refresh on 401', async () => {
    let calls = 0
    server.use(
      http.get(`${API_BASE}/api/artifacts/a1/download`, () => {
        calls += 1
        if (calls === 1) return new HttpResponse(null, { status: 401 })
        return new HttpResponse(new Uint8Array([1, 2, 3]), {
          headers: { 'Content-Type': 'application/octet-stream' },
        })
      }),
      http.post(`${API_BASE}/api/auth/refresh`, () => HttpResponse.json({})),
    )

    const blob = await apiFetchBinary(`${API_BASE}/api/artifacts/a1/download`)
    expect(calls).toBe(2)
    expect(new Uint8Array(await blob.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]))
  })

  it('throws a structured ApiError on a non-OK response', async () => {
    server.use(
      http.get(`${API_BASE}/api/artifacts/a2/download`, () =>
        HttpResponse.json(
          { error: { code: 'artifact_not_found', message: 'Not found' } },
          { status: 404 },
        ),
      ),
    )

    const error = await apiFetchBinary('/api/artifacts/a2/download').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
    expect((error as ApiError).code).toBe('artifact_not_found')
  })
})

describe('org signal across the 401 → refresh → retry', () => {
  it.each([
    ['apiFetch', () => apiFetch('/api/agents/retry-probe')],
    ['apiFetchBinary', () => apiFetchBinary('/api/agents/retry-probe')],
  ])('%s does not retry under the next organization', async (_name, call) => {
    let calls = 0
    server.use(
      http.get(`${API_BASE}/api/agents/retry-probe`, () => {
        calls += 1
        return calls === 1
          ? new HttpResponse(null, { status: 401 })
          : HttpResponse.json({ leaked: true })
      }),
      http.post(`${API_BASE}/api/auth/refresh`, () => {
        // The organization switches while the refresh is in flight.
        beginOrgSwitch()
        finishOrgSwitch()
        return HttpResponse.json({})
      }),
    )

    await expect(call()).rejects.toMatchObject({ name: 'AbortError' })
    expect(calls).toBe(1)
  })
})
