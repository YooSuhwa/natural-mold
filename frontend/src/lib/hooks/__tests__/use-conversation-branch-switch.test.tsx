import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { conversationsApi } from '@/lib/api/conversations'
import { conversationKeys } from '@/lib/hooks/use-conversations'
import { dispatchMoldyBranchSwitched } from '@/lib/chat/langgraph-runtime/branch-switch-events'
import { useConversationBranchSwitch } from '@/lib/hooks/use-conversation-branch-switch'

vi.mock('@/lib/api/conversations', () => ({
  conversationsApi: { switchBranch: vi.fn() },
}))

vi.mock('@/lib/chat/langgraph-runtime/branch-switch-events', () => ({
  dispatchMoldyBranchSwitched: vi.fn(),
}))

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { readonly children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useConversationBranchSwitch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('switches, refetches active messages, then dispatches the runtime event', async () => {
    const events: string[] = []
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    vi.mocked(conversationsApi.switchBranch).mockImplementation(async () => {
      events.push('switch')
    })
    vi.spyOn(queryClient, 'refetchQueries').mockImplementation(async (filters) => {
      events.push('refetch')
      expect(filters).toEqual({
        queryKey: conversationKeys.messages('conversation-1'),
        type: 'active',
      })
      return undefined
    })
    vi.mocked(dispatchMoldyBranchSwitched).mockImplementation(() => {
      events.push('dispatch')
    })

    const { result } = renderHook(() => useConversationBranchSwitch('conversation-1'), {
      wrapper: createWrapper(queryClient),
    })
    await act(async () => result.current('checkpoint-2'))

    expect(events).toEqual(['switch', 'refetch', 'dispatch'])
    expect(dispatchMoldyBranchSwitched).toHaveBeenCalledWith({
      conversationId: 'conversation-1',
      checkpointId: 'checkpoint-2',
    })
  })

  it('does nothing without a conversation id', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useConversationBranchSwitch(null), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => result.current('checkpoint-2'))

    expect(conversationsApi.switchBranch).not.toHaveBeenCalled()
    expect(dispatchMoldyBranchSwitched).not.toHaveBeenCalled()
  })
})
