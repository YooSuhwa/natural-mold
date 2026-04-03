import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../setup'
import { apiFetch, ApiError } from '@/lib/api/client'

const API_BASE = 'http://localhost:8001'

describe('apiFetch', () => {
  it('returns parsed JSON on success', async () => {
    server.use(
      http.get(`${API_BASE}/api/test`, () => {
        return HttpResponse.json({ message: 'ok' })
      }),
    )

    const result = await apiFetch<{ message: string }>('/api/test')
    expect(result).toEqual({ message: 'ok' })
  })

  it('returns undefined for 204 No Content', async () => {
    server.use(
      http.delete(`${API_BASE}/api/test/1`, () => {
        return new HttpResponse(null, { status: 204 })
      }),
    )

    const result = await apiFetch<void>('/api/test/1', { method: 'DELETE' })
    expect(result).toBeUndefined()
  })

  it('throws ApiError with detail on 400 (old format)', async () => {
    server.use(
      http.post(`${API_BASE}/api/test`, () => {
        return HttpResponse.json({ detail: 'Validation failed' }, { status: 400 })
      }),
    )

    await expect(apiFetch('/api/test', { method: 'POST' })).rejects.toThrow(ApiError)
    await expect(apiFetch('/api/test', { method: 'POST' })).rejects.toThrow('Validation failed')
  })

  it('throws ApiError with code on 400 (new format)', async () => {
    server.use(
      http.post(`${API_BASE}/api/test`, () => {
        return HttpResponse.json(
          { error: { code: 'VALIDATION_ERROR', message: 'Invalid input' } },
          { status: 400 },
        )
      }),
    )

    try {
      await apiFetch('/api/test', { method: 'POST' })
      expect.unreachable('Should have thrown')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      const apiErr = error as ApiError
      expect(apiErr.status).toBe(400)
      expect(apiErr.code).toBe('VALIDATION_ERROR')
      expect(apiErr.message).toBe('Invalid input')
    }
  })

  it('throws ApiError on 404 (old format)', async () => {
    server.use(
      http.get(`${API_BASE}/api/missing`, () => {
        return HttpResponse.json({ detail: 'Not found' }, { status: 404 })
      }),
    )

    try {
      await apiFetch('/api/missing')
      expect.unreachable('Should have thrown')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      const apiErr = error as ApiError
      expect(apiErr.status).toBe(404)
      expect(apiErr.code).toBe('UNKNOWN_ERROR')
      expect(apiErr.message).toBe('Not found')
    }
  })

  it('throws ApiError on 500 with fallback message', async () => {
    server.use(
      http.get(`${API_BASE}/api/error`, () => {
        return HttpResponse.json({}, { status: 500 })
      }),
    )

    try {
      await apiFetch('/api/error')
      expect.unreachable('Should have thrown')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      const apiErr = error as ApiError
      expect(apiErr.status).toBe(500)
      expect(apiErr.code).toBe('UNKNOWN_ERROR')
      expect(apiErr.message).toBe('Unknown error')
    }
  })

  it('has name set to ApiError', async () => {
    server.use(
      http.get(`${API_BASE}/api/name-test`, () => {
        return HttpResponse.json({}, { status: 500 })
      }),
    )

    try {
      await apiFetch('/api/name-test')
      expect.unreachable('Should have thrown')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      expect((error as ApiError).name).toBe('ApiError')
    }
  })

  it('sends default Content-Type header', async () => {
    let capturedContentType: string | null = null
    server.use(
      http.get(`${API_BASE}/api/headers`, ({ request }) => {
        capturedContentType = request.headers.get('Content-Type')
        return HttpResponse.json({})
      }),
    )

    await apiFetch('/api/headers')
    expect(capturedContentType).toBe('application/json')
  })

  it('merges custom headers with defaults', async () => {
    let capturedHeaders: Record<string, string | null> = {}
    server.use(
      http.get(`${API_BASE}/api/custom-headers`, ({ request }) => {
        capturedHeaders = {
          'Content-Type': request.headers.get('Content-Type'),
          'X-Custom': request.headers.get('X-Custom'),
        }
        return HttpResponse.json({})
      }),
    )

    await apiFetch('/api/custom-headers', {
      headers: { 'X-Custom': 'test-value' },
    })
    expect(capturedHeaders['Content-Type']).toBe('application/json')
    expect(capturedHeaders['X-Custom']).toBe('test-value')
  })

  it('sends POST with JSON body', async () => {
    let capturedBody: unknown = null
    server.use(
      http.post(`${API_BASE}/api/post-test`, async ({ request }) => {
        capturedBody = await request.json()
        return HttpResponse.json({ id: '1' })
      }),
    )

    const result = await apiFetch<{ id: string }>('/api/post-test', {
      method: 'POST',
      body: JSON.stringify({ name: 'test' }),
    })
    expect(result).toEqual({ id: '1' })
    expect(capturedBody).toEqual({ name: 'test' })
  })
})
