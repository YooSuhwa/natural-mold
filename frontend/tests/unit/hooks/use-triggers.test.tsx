import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import {
  useTriggers,
  useCreateTrigger,
  useUpdateTrigger,
  useDeleteTrigger,
} from '@/lib/hooks/use-triggers'
import { mockTriggerList } from '../../mocks/fixtures'

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return Wrapper
}

describe('useTriggers', () => {
  it('fetches trigger list by agentId', async () => {
    const { result } = renderHook(() => useTriggers('agent-1'), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(mockTriggerList)
  })

  it('returns loading state initially', () => {
    const { result } = renderHook(() => useTriggers('agent-1'), {
      wrapper: createWrapper(),
    })
    expect(result.current.isLoading).toBe(true)
  })
})

describe('useCreateTrigger', () => {
  it('creates a trigger and returns response', async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useCreateTrigger('agent-1'), { wrapper })
    let response: unknown

    await act(async () => {
      response = await result.current.mutateAsync({
        trigger_type: 'interval',
        schedule_config: { interval_minutes: 30 },
        input_message: 'Check status',
      })
    })

    expect(response).toMatchObject({
      id: 'trigger-new',
      agent_id: 'agent-1',
      trigger_type: 'interval',
    })
  })
})

describe('useUpdateTrigger', () => {
  it('updates a trigger and returns response', async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useUpdateTrigger('agent-1'), { wrapper })
    let response: unknown

    await act(async () => {
      response = await result.current.mutateAsync({
        triggerId: 'trigger-1',
        data: { input_message: 'Updated message' },
      })
    })

    expect(response).toMatchObject({
      id: 'trigger-1',
      input_message: 'Updated message',
    })
  })
})

describe('useDeleteTrigger', () => {
  it('deletes a trigger without error', async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useDeleteTrigger('agent-1'), { wrapper })

    await act(async () => {
      await expect(result.current.mutateAsync('trigger-1')).resolves.not.toThrow()
    })
  })
})
