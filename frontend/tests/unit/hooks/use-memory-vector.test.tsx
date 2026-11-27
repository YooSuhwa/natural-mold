import { act, renderHook, waitFor } from '@testing-library/react'
import { onlineManager, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { memoryApi } from '@/lib/api/memory'
import { memoryKeys, useUpdateMemoryVector } from '@/lib/hooks/use-memory'
import { beginOrgSwitch, finishOrgSwitch } from '@/lib/org-scope/org-scope-signal'
import type { MemoryVectorStatus, MemoryVectorUpdate } from '@/lib/types/memory'

const update: MemoryVectorUpdate = { model_id: 'model-a', confirm_count: 0, recall_mode: 'serve' }
const newOrgStatus: MemoryVectorStatus = {
  model: {
    id: 'model-a',
    model_name: 'embed',
    display_name: 'Company model',
    provider: 'openai',
    dimensions: 512,
    source: 'company',
  },
  candidates: [],
  total: 0,
  embedded: 0,
  other_vectors: 0,
  job_state: null,
  job_progress: null,
  floor_measured: true,
  quality_floor: 0.42,
  recall_mode: 'off',
  can_manage: true,
}

function setup() {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return { queryClient, ...renderHook(() => useUpdateMemoryVector(), { wrapper: Wrapper }) }
}

describe('memory vector saves across organization switches', () => {
  afterEach(() => {
    onlineManager.setOnline(true)
    vi.restoreAllMocks()
  })

  it('keeps the new organization’s mode when a previous request finishes after the switch', async () => {
    const response = Promise.withResolvers<MemoryVectorStatus>()
    const send = vi.spyOn(memoryApi, 'updateVector').mockReturnValue(response.promise)
    const { result, queryClient, unmount } = setup()
    act(() => result.current.mutate(update))
    await waitFor(() => expect(send).toHaveBeenCalledWith(update))

    beginOrgSwitch()
    unmount()
    finishOrgSwitch()
    queryClient.setQueryData(memoryKeys.vector, newOrgStatus)
    response.resolve({ ...newOrgStatus, recall_mode: 'serve' })
    await waitFor(() =>
      expect(queryClient.getMutationCache().getAll()[0].state.status).not.toBe('pending'),
    )
    expect(queryClient.getQueryData(memoryKeys.vector)).toEqual(newOrgStatus)
    expect(queryClient.getQueryState(memoryKeys.vector)?.isInvalidated).toBe(false)
  })

  it('drops a queued mutation from the previous organization on reconnect', async () => {
    const send = vi.spyOn(memoryApi, 'updateVector').mockResolvedValue(newOrgStatus)
    onlineManager.setOnline(false)
    const { result, queryClient, unmount } = setup()
    act(() => result.current.mutate(update))
    await waitFor(() => expect(result.current.isPaused).toBe(true))

    beginOrgSwitch()
    unmount()
    finishOrgSwitch()
    onlineManager.setOnline(true)
    await queryClient.resumePausedMutations()
    await waitFor(() =>
      expect(queryClient.getMutationCache().getAll()[0].state.error).toMatchObject({
        name: 'AbortError',
      }),
    )
    expect(send).not.toHaveBeenCalled()
  })
})
