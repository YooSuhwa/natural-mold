import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import {
  conversationKeys,
  invalidateConversationNavigators,
  useConversations,
  useConversationDetail,
  useConversationPages,
  useGlobalConversationPages,
  useMessages,
  useCreateConversation,
  useMarkConversationRead,
} from '@/lib/hooks/use-conversations'
import {
  mockConversationList,
  mockConversationPage,
  mockGlobalConversationPage,
  mockMessageList,
} from '../../mocks/fixtures'

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

describe('useConversationPages', () => {
  it('fetches the first server-paginated conversation page', async () => {
    const { result } = renderHook(
      () => useConversationPages('agent-1', { limit: 30, q: 'research' }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.pages[0]).toEqual(mockConversationPage)
  })

  it('stays idle when page loading is disabled', () => {
    const { result } = renderHook(
      () => useConversationPages('agent-1', { limit: 30 }, { enabled: false }),
      { wrapper: createWrapper() },
    )

    expect(result.current.fetchStatus).toBe('idle')
  })
})

describe('useGlobalConversationPages', () => {
  it('fetches global conversations with embedded agents', async () => {
    const { result } = renderHook(
      () => useGlobalConversationPages({ limit: 30, q: 'conversation', sort: 'updated' }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.pages[0]).toEqual(mockGlobalConversationPage)
    expect(result.current.hasNextPage).toBe(true)
  })

  it('stays idle when global page loading is disabled', () => {
    const { result } = renderHook(
      () => useGlobalConversationPages({ limit: 30 }, { enabled: false }),
      { wrapper: createWrapper() },
    )

    expect(result.current.fetchStatus).toBe('idle')
  })
})

describe('useConversationDetail', () => {
  it('fetches a single conversation with agent metadata', async () => {
    const { result } = renderHook(() => useConversationDetail('conv-1'), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.agent.name).toBe('Test Agent')
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

describe('invalidateConversationNavigators', () => {
  it('invalidates agent pages, global pages, and agent summaries', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const pageKey = conversationKeys.pages('agent-1', {
      limit: 30,
      q: undefined,
      sort: 'updated',
    })
    const globalKey = conversationKeys.globalPages({
      limit: 30,
      q: undefined,
      sort: 'updated',
    })
    queryClient.setQueryData(conversationKeys.list('agent-1'), mockConversationList)
    queryClient.setQueryData(pageKey, mockConversationPage)
    queryClient.setQueryData(globalKey, mockGlobalConversationPage)
    queryClient.setQueryData(['agents', 'summary'], [])

    invalidateConversationNavigators(queryClient, 'agent-1')

    expect(queryClient.getQueryState(conversationKeys.list('agent-1'))?.isInvalidated).toBe(true)
    expect(queryClient.getQueryState(pageKey)?.isInvalidated).toBe(true)
    expect(queryClient.getQueryState(globalKey)?.isInvalidated).toBe(true)
    expect(queryClient.getQueryState(['agents', 'summary'])?.isInvalidated).toBe(true)
  })

  it('invalidates the conversation detail when a conversation id is provided', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    queryClient.setQueryData(conversationKeys.detail('conv-1'), mockConversationList[0])
    queryClient.setQueryData(conversationKeys.detail('conv-2'), mockConversationList[1])

    invalidateConversationNavigators(queryClient, 'agent-1', 'conv-1')

    expect(queryClient.getQueryState(conversationKeys.detail('conv-1'))?.isInvalidated).toBe(true)
    expect(queryClient.getQueryState(conversationKeys.detail('conv-2'))?.isInvalidated).toBe(false)
  })

  it('does not invalidate unrelated agent queries beyond summaries', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    queryClient.setQueryData(['agents'], [])
    queryClient.setQueryData(['agents', 'agent-1'], { id: 'agent-1' })

    invalidateConversationNavigators(queryClient, 'agent-1')

    expect(queryClient.getQueryState(['agents'])?.isInvalidated).toBe(false)
    expect(queryClient.getQueryState(['agents', 'agent-1'])?.isInvalidated).toBe(false)
  })
})
