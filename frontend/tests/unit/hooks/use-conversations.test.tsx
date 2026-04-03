import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useConversations, useMessages, useCreateConversation } from '@/lib/hooks/use-conversations'
import { mockConversationList, mockMessageList } from '../../mocks/fixtures'

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return Wrapper
}

describe('useConversations', () => {
  it('fetches conversations by agentId', async () => {
    const { result } = renderHook(() => useConversations('agent-1'), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(mockConversationList)
  })

  it('does not fetch when agentId is empty', () => {
    const { result } = renderHook(() => useConversations(''), {
      wrapper: createWrapper(),
    })
    expect(result.current.fetchStatus).toBe('idle')
  })

  it('returns loading state initially', () => {
    const { result } = renderHook(() => useConversations('agent-1'), {
      wrapper: createWrapper(),
    })
    expect(result.current.isLoading).toBe(true)
  })
})

describe('useMessages', () => {
  it('fetches messages by conversationId', async () => {
    const { result } = renderHook(() => useMessages('conv-1'), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(mockMessageList)
  })

  it('does not fetch when conversationId is empty', () => {
    const { result } = renderHook(() => useMessages(''), {
      wrapper: createWrapper(),
    })
    expect(result.current.fetchStatus).toBe('idle')
  })
})

describe('useCreateConversation', () => {
  it('creates a conversation and returns response', async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useCreateConversation('agent-1'), {
      wrapper,
    })
    let response: unknown

    await act(async () => {
      response = await result.current.mutateAsync('Test Title')
    })

    expect(response).toMatchObject({ id: 'conv-new', agent_id: 'agent-1' })
  })

  it('creates a conversation without title', async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useCreateConversation('agent-1'), {
      wrapper,
    })
    let response: unknown

    await act(async () => {
      response = await result.current.mutateAsync(undefined)
    })

    expect(response).toMatchObject({ id: 'conv-new' })
  })
})
