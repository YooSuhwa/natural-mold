import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiFetchBinary } from '@/lib/api/client'
import { downloadApiFile, useApiObjectUrl } from '@/lib/hooks/use-api-object-url'

vi.mock('@/lib/api/client', () => ({ apiFetchBinary: vi.fn() }))

describe('useApiObjectUrl', () => {
  afterEach(() => vi.restoreAllMocks())

  it('loads through apiFetchBinary and revokes the object URL on unmount', async () => {
    vi.mocked(apiFetchBinary).mockResolvedValue(new Blob(['x']))
    const create = vi.fn(() => 'blob:one')
    const revoke = vi.fn()
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke })

    const { result, unmount } = renderHook(() => useApiObjectUrl('/api/files/a.png'))
    expect(result.current.status).toBe('loading')
    await waitFor(() => expect(result.current).toEqual({ status: 'ready', objectUrl: 'blob:one' }))
    expect(apiFetchBinary).toHaveBeenCalledWith('/api/files/a.png', expect.any(AbortSignal))

    unmount()
    expect(revoke).toHaveBeenCalledWith('blob:one')
    const signal = vi.mocked(apiFetchBinary).mock.calls[0]?.[1]
    expect(signal?.aborted).toBe(true)
  })

  it('reports errors and stays idle without a url', async () => {
    vi.mocked(apiFetchBinary).mockRejectedValue(new Error('404'))
    const { result } = renderHook(() => useApiObjectUrl('/api/files/missing.png'))
    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(renderHook(() => useApiObjectUrl(null)).result.current.status).toBe('idle')
  })
})

it('does not save bytes after the original organization has changed', async () => {
  const controller = new AbortController()
  const create = vi.fn()
  Object.assign(URL, { createObjectURL: create })
  vi.mocked(apiFetchBinary).mockImplementationOnce(async () => {
    controller.abort()
    return new Blob(['from the previous organization'])
  })
  await expect(
    downloadApiFile('/api/notifications/n/result', 'file.csv', controller.signal),
  ).rejects.toMatchObject({ name: 'AbortError' })
  expect(create).not.toHaveBeenCalled()
})
