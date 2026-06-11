import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import { conversationKeys } from '../use-conversations'
import { useCancelConversationRun } from '../use-conversation-runs'

vi.mock('@/lib/api/conversation-runs', () => ({
  conversationRunsApi: { cancel: vi.fn() },
}))

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useConversationRuns', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('cancel mutation calls the run cancel API and invalidates conversation caches', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    vi.mocked(conversationRunsApi.cancel).mockResolvedValue({
      id: 'run-1',
      conversation_id: 'conversation-1',
      agent_id: 'agent-1',
      status: 'canceling',
      source: 'chat',
      parent_run_id: null,
      worker_instance_id: null,
      interrupt_id: null,
      last_event_id: null,
      input_preview: null,
      error_code: null,
      error_message: null,
      cancel_requested_at: null,
      started_at: null,
      heartbeat_at: null,
      completed_at: null,
      created_at: '2026-06-11T00:00:00.000Z',
      updated_at: '2026-06-11T00:00:00.000Z',
    })

    const { result } = renderHook(() => useCancelConversationRun('conversation-1'), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync('run-1')
    })

    expect(conversationRunsApi.cancel).toHaveBeenCalledWith('conversation-1', 'run-1')
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: conversationKeys.messages('conversation-1'),
      })
    })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['agents'] })
  })
})
