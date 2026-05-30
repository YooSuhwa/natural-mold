import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import {
  conversationKeys,
  useConversations,
  useMessages,
  useCreateConversation,
  useMarkConversationRead,
} from '@/lib/hooks/use-conversations'
import { mockConversationList, mockMessageList } from '../../mocks/fixtures'

function createWrapperWithClient() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return { Wrapper, queryClient }
}

function createWrapper() {
  return createWrapperWithClient().Wrapper
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

describe('useMarkConversationRead', () => {
  it('updates the cached conversation list when read state is cleared', async () => {
    const { Wrapper, queryClient } = createWrapperWithClient()
    queryClient.setQueryData(conversationKeys.list('agent-1'), [
      { ...mockConversationList[0], unread_count: 2, last_read_at: null },
      mockConversationList[1],
    ])
    const { result } = renderHook(() => useMarkConversationRead('agent-1'), {
      wrapper: Wrapper,
    })

    await act(async () => {
      await result.current.mutateAsync('conv-1')
    })

    const conversations = queryClient.getQueryData(conversationKeys.list('agent-1'))
    expect(conversations?.find((conversation) => conversation.id === 'conv-1')).toMatchObject({
      unread_count: 0,
      last_read_at: '2026-01-01T01:00:00Z',
    })
  })
})
