import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { InfiniteData } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useConversationTitle } from '@/lib/hooks/use-conversation-title'
import { conversationKeys } from '@/lib/hooks/use-conversations'
import type {
  Conversation,
  ConversationListEnvelope,
  ConversationWithAgentListEnvelope,
} from '@/lib/types'
import {
  mockConversation,
  mockConversationPage,
  mockGlobalConversationPage,
} from '../../mocks/fixtures'

const pageParams = { limit: 30, q: undefined, sort: 'updated' as const }

function createWrapperWithClient() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return { Wrapper, queryClient }
}

function seedAgentPages(queryClient: QueryClient, title: string) {
  queryClient.setQueryData<InfiniteData<ConversationListEnvelope>>(
    conversationKeys.pages('agent-1', pageParams),
    {
      pages: [{ ...mockConversationPage, items: [{ ...mockConversation, title }] }],
      pageParams: [undefined],
    },
  )
}

function seedGlobalPages(queryClient: QueryClient, title: string) {
  queryClient.setQueryData<InfiniteData<ConversationWithAgentListEnvelope>>(
    conversationKeys.globalPages(pageParams),
    {
      pages: [
        {
          ...mockGlobalConversationPage,
          items: mockGlobalConversationPage.items.map((item) =>
            item.id === 'conv-1' ? { ...item, title } : item,
          ),
        },
      ],
      pageParams: [undefined],
    },
  )
}

describe('useConversationTitle', () => {
  it('prefers the agent list cache over page caches without fetching', () => {
    const { Wrapper, queryClient } = createWrapperWithClient()
    queryClient.setQueryData<Conversation[]>(conversationKeys.list('agent-1'), [
      { ...mockConversation, title: 'List Title' },
    ])
    seedAgentPages(queryClient, 'Page Title')
    seedGlobalPages(queryClient, 'Global Title')

    const { result } = renderHook(() => useConversationTitle('agent-1', 'conv-1'), {
      wrapper: Wrapper,
    })

    expect(result.current).toBe('List Title')
    // 캐시 적중 시 detail 쿼리는 비활성으로 남아 fetch가 발생하지 않는다
    expect(queryClient.getQueryState(conversationKeys.detail('conv-1'))?.fetchStatus).toBe('idle')
  })

  it('falls back to the agent infinite page cache when the list cache misses', () => {
    const { Wrapper, queryClient } = createWrapperWithClient()
    seedAgentPages(queryClient, 'Page Title')
    seedGlobalPages(queryClient, 'Global Title')

    const { result } = renderHook(() => useConversationTitle('agent-1', 'conv-1'), {
      wrapper: Wrapper,
    })

    expect(result.current).toBe('Page Title')
  })

  it('falls back to the global page cache when agent caches miss', () => {
    const { Wrapper, queryClient } = createWrapperWithClient()
    seedGlobalPages(queryClient, 'Global Title')

    const { result } = renderHook(() => useConversationTitle('agent-1', 'conv-1'), {
      wrapper: Wrapper,
    })

    expect(result.current).toBe('Global Title')
  })

  it('fetches the conversation detail when every cache misses', async () => {
    const { Wrapper } = createWrapperWithClient()

    const { result } = renderHook(
      () => useConversationTitle('agent-1', 'conv-1', 'Fallback Agent'),
      { wrapper: Wrapper },
    )

    // fetch가 끝나기 전에는 에이전트 이름으로 폴백한다
    expect(result.current).toBe('Fallback Agent')
    await waitFor(() => expect(result.current).toBe('Test Conversation'))
  })

  it('does not fetch for the local draft id and keeps the agent name', () => {
    const { Wrapper, queryClient } = createWrapperWithClient()

    const { result } = renderHook(() => useConversationTitle('agent-1', 'new', 'Test Agent'), {
      wrapper: Wrapper,
    })

    expect(result.current).toBe('Test Agent')
    expect(queryClient.getQueryState(conversationKeys.detail('new'))?.fetchStatus).toBe('idle')
  })
})
