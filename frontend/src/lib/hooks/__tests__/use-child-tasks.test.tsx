import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/lib/api/errors'
import { getChildTask, listChildTasks, type ChildTask } from '@/lib/api/child-tasks'
import { currentOrgScope } from '@/lib/org-scope/org-scope-signal'
import { conversationQueryKeys } from '@/lib/query-keys/conversations'
import { useChildTask, useChildTasks } from '../use-child-tasks'

vi.mock('@/lib/api/child-tasks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/child-tasks')>()),
  getChildTask: vi.fn(),
  listChildTasks: vi.fn(),
}))

const task: ChildTask = {
  id: 'child',
  run_id: 'run',
  tool_call_id: 'call',
  role: 'researcher',
  status: 'running',
  created_at: '2026-11-27T01:02:03Z',
  updated_at: '2026-11-27T01:03:03Z',
  checkpointed: true,
  result: null,
  instructions: [],
}

function wrapper(client = new QueryClient()) {
  return function QueryWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

describe('child task readers', () => {
  beforeEach(() => vi.resetAllMocks())

  it('lets the user continue an empty page and deduplicates tasks that change position', async () => {
    const list = vi.mocked(listChildTasks)
    list.mockResolvedValueOnce({
      items: [],
      next_offset: 50,
      enabled: true,
      allow_legacy_details: false,
    })
    list.mockResolvedValueOnce({
      items: [task],
      next_offset: 100,
      enabled: true,
      allow_legacy_details: false,
    })
    list.mockResolvedValueOnce({
      items: [task, { ...task, id: 'sibling' }],
      next_offset: null,
      enabled: true,
      allow_legacy_details: false,
    })

    const { result } = renderHook(() => useChildTasks('conversation'), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.hasNextPage).toBe(true))
    expect(result.current.tasks).toEqual([])
    expect(list).toHaveBeenCalledTimes(1)
    await act(async () => {
      await result.current.fetchNextPage()
    })
    await waitFor(() => expect(result.current.tasks).toHaveLength(1))
    await act(async () => {
      await result.current.fetchNextPage()
    })
    await waitFor(() => expect(result.current.hasNextPage).toBe(false))
    expect(result.current.tasks.map((item) => item.id)).toEqual(['child', 'sibling'])
    expect(list.mock.calls.map((args) => args[2])).toEqual([0, 50, 100])
  })

  it('permits legacy details only when every revalidated page explicitly allows them', async () => {
    const list = vi.mocked(listChildTasks)
    list.mockResolvedValueOnce({
      items: [],
      next_offset: 50,
      enabled: false,
      allow_legacy_details: true,
    })
    list.mockResolvedValueOnce({
      items: [],
      next_offset: null,
      enabled: false,
      allow_legacy_details: false,
    })
    const { result } = renderHook(() => useChildTasks('conversation'), { wrapper: wrapper() })
    expect(result.current.allowLegacyDetails).toBe(false)
    await waitFor(() => expect(result.current.allowLegacyDetails).toBe(true))
    await act(async () => {
      await result.current.fetchNextPage()
    })
    await waitFor(() => expect(result.current.allowLegacyDetails).toBe(false))
    list.mockResolvedValueOnce({
      items: [],
      next_offset: null,
      enabled: true,
      allow_legacy_details: true,
    })
    await act(async () => {
      await result.current.refetch()
    })
    expect(result.current.allowLegacyDetails).toBe(false)
  })

  it('restores the canonical page on the first automatic retry after a temporary outage', async () => {
    const list = vi.mocked(listChildTasks)
    list.mockRejectedValueOnce(new ApiError(503, 'busy', 'Unavailable'))
    list.mockResolvedValueOnce({
      items: [task],
      next_offset: null,
      enabled: false,
      allow_legacy_details: false,
    })

    const { result } = renderHook(() => useChildTasks('conversation'), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.tasks).toEqual([task]), { timeout: 4000 })
    expect(result.current.enabled).toBe(false)
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('reauthorizes cached detail on opening the panel before exposing prior results', async () => {
    const client = new QueryClient()
    const key = conversationQueryKeys.childTask(
      'conversation',
      currentOrgScope().generation,
      'child',
    )
    client.setQueryData(key, { ...task, result: 'previously visible' })
    let deny: ((error: Error) => void) | undefined
    vi.mocked(getChildTask).mockReturnValue(
      new Promise((_resolve, reject) => {
        deny = reject
      }),
    )
    const { result } = renderHook(() => useChildTask('conversation', 'child'), {
      wrapper: wrapper(client),
    })
    expect(result.current.isPending).toBe(true)
    expect(result.current.data).toBeUndefined()
    await act(async () => {
      deny?.(new ApiError(404, 'not_found', 'Missing'))
    })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.data).toBeUndefined()
  })

  it('hides stale gated detail immediately on a denied read without retrying the request', async () => {
    const get = vi.mocked(getChildTask)
    get.mockResolvedValueOnce(task)
    get.mockRejectedValueOnce(new ApiError(404, 'not_found', 'Missing'))
    const { result } = renderHook(() => useChildTask('conversation', 'child'), {
      wrapper: wrapper(),
    })
    await waitFor(() => expect(result.current.data).toEqual(task))
    await act(async () => {
      await result.current.refetch()
    })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.data).toBeUndefined()
    expect(get).toHaveBeenCalledTimes(2)
  })
})
