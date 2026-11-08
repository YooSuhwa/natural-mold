import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { useWorkflowRunApproval } from '@/lib/hooks/use-approvals'

const list = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api/approvals', () => ({
  approvalsApi: { list },
  emergencyStopsApi: {},
}))

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

const request = { id: 'req-1', kind: 'business', workflow_run_id: 'run-1' }

describe('useWorkflowRunApproval', () => {
  beforeEach(() => {
    list.mockReset()
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })
  afterEach(() => vi.useRealTimers())

  it('keeps asking until the request of a run that just started waiting shows up', async () => {
    // The page opened while the run was still classifying; the inbox has nothing yet.
    list.mockResolvedValueOnce({ items: [] }).mockResolvedValue({ items: [request] })
    const { result } = renderHook(
      () => useWorkflowRunApproval('run-1', 'waiting', 'review', true),
      { wrapper: wrapper() },
    )
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1))
    expect(result.current.data ?? null).toBeNull()

    await act(() => vi.advanceTimersByTimeAsync(3_000))
    await waitFor(() => expect(result.current.data).toEqual(request))

    // Found: no more polling.
    await act(() => vi.advanceTimersByTimeAsync(9_000))
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('asks again at a new waiting point and not at all while the run is moving', async () => {
    list.mockResolvedValue({ items: [request] })
    const { rerender } = renderHook(
      ({ status, node, on }) => useWorkflowRunApproval('run-1', status, node, on),
      { wrapper: wrapper(), initialProps: { status: 'running', node: 'classify', on: false } },
    )
    await act(() => vi.advanceTimersByTimeAsync(3_000))
    expect(list).not.toHaveBeenCalled()

    rerender({ status: 'waiting', node: 'review', on: true })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1))
    rerender({ status: 'unknown', node: 'draft', on: true })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
  })
})
