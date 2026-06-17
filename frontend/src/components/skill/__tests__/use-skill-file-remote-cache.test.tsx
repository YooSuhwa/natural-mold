import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useSkillFileRemoteCache } from '../use-skill-file-remote-cache'

function textResponse(body: string): Response {
  return new Response(body, { status: 200 })
}

describe('useSkillFileRemoteCache', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('refetches the selected text file when the skill cache key changes', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(textResponse('old skill md'))
      .mockResolvedValueOnce(textResponse('new skill md'))
    const onLoadError = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const { result, rerender } = renderHook(
      ({ cacheKey }: { cacheKey: string }) =>
        useSkillFileRemoteCache({
          skillId: 'skill-1',
          cacheKey,
          selectedPath: 'SKILL.md',
          onLoadError,
        }),
      { initialProps: { cacheKey: 'hash-1' } },
    )

    await waitFor(() => {
      expect(result.current.remoteCache.get('SKILL.md')).toBe('old skill md')
    })

    rerender({ cacheKey: 'hash-2' })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2)
    })
    await waitFor(() => {
      expect(result.current.remoteCache.get('SKILL.md')).toBe('new skill md')
    })
    expect(onLoadError).not.toHaveBeenCalled()
  })
})
