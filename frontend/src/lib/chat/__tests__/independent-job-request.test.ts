import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { pendingJobRequest } from '../independent-job-request'
import { clearOrgScopedStorage } from '@/lib/org-scope/org-scoped-storage'

describe('pending independent job requests', () => {
  beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto)
    sessionStorage.clear()
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('keeps two proven sibling tasks distinct and clears all pending request IDs on organization switch', async () => {
    const pending = new Map<string, string>()
    const first = await pendingJobRequest('run', 'agent', 'same work', pending, 'child-a')
    const second = await pendingJobRequest('run', 'agent', 'same work', pending, 'child-b')
    expect(first.id).not.toBe(second.id)
    sessionStorage.setItem('unrelated-display-preference', 'wide')
    clearOrgScopedStorage()
    expect(Object.keys(sessionStorage)).toEqual(['unrelated-display-preference'])
  })

  it('reuses an uncertain request after a network failure when session storage is blocked', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })
    const pending = new Map<string, string>()
    const first = await pendingJobRequest('run', 'agent', 'same work', pending)
    const retry = await pendingJobRequest('run', 'agent', 'same work', pending)
    expect(first.id).toBe(retry.id)
    retry.confirm()
    expect((await pendingJobRequest('run', 'agent', 'same work', pending)).id).not.toBe(first.id)
  })
})
