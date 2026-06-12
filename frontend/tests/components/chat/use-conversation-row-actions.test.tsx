import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { InfiniteData } from '@tanstack/react-query'
import { NextIntlClientProvider } from 'next-intl'
import type { ReactNode } from 'react'
import { useConversationRowActions } from '@/components/chat/use-conversation-row-actions'
import { conversationKeys } from '@/lib/hooks/use-conversations'
import type {
  Conversation,
  ConversationListEnvelope,
  ConversationWithAgentListEnvelope,
} from '@/lib/types'
import messages from '../../../messages/ko.json'
import {
  mockConversation,
  mockConversationList,
  mockConversationPage,
  mockGlobalConversationPage,
} from '../../mocks/fixtures'

const apiMocks = vi.hoisted(() => ({
  update: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/lib/api/conversations', () => ({
  conversationsApi: {
    update: apiMocks.update,
    delete: apiMocks.delete,
  },
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

const pageParams = { limit: 30, q: undefined, sort: 'updated' as const }
const listKey = conversationKeys.list('agent-1')
const pageKey = conversationKeys.pages('agent-1', pageParams)
const globalKey = conversationKeys.globalPages(pageParams)

function createWrapperWithClient() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <NextIntlClientProvider locale="ko" messages={messages}>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </NextIntlClientProvider>
    )
  }
  return { Wrapper, queryClient }
}

/** 내비게이터가 쓰는 세 가지 캐시 모양(목록 배열, agent infinite, global infinite)을 시드한다. */
function seedNavigatorCaches(queryClient: QueryClient) {
  queryClient.setQueryData<Conversation[]>(listKey, mockConversationList)
  queryClient.setQueryData<InfiniteData<ConversationListEnvelope>>(pageKey, {
    pages: [mockConversationPage],
    pageParams: [undefined],
  })
  queryClient.setQueryData<InfiniteData<ConversationWithAgentListEnvelope>>(globalKey, {
    pages: [mockGlobalConversationPage],
    pageParams: [undefined],
  })
}

function pinnedInList(queryClient: QueryClient, conversationId: string) {
  return queryClient
    .getQueryData<Conversation[]>(listKey)
    ?.find((item) => item.id === conversationId)?.is_pinned
}

describe('useConversationRowActions', () => {
  beforeEach(() => {
    apiMocks.update.mockReset()
    apiMocks.delete.mockReset()
  })

  it('optimistically patches every navigator cache shape when toggling pin', async () => {
    const { Wrapper, queryClient } = createWrapperWithClient()
    seedNavigatorCaches(queryClient)
    apiMocks.update.mockResolvedValue({ ...mockConversation, is_pinned: true })

    const { result } = renderHook(
      () => useConversationRowActions({ activeConversationId: null }),
      { wrapper: Wrapper },
    )

    act(() => result.current.togglePin(mockConversation))

    await waitFor(() => expect(pinnedInList(queryClient, 'conv-1')).toBe(true))
    expect(apiMocks.update).toHaveBeenCalledWith('conv-1', { is_pinned: true })

    const agentPages = queryClient.getQueryData<InfiniteData<ConversationListEnvelope>>(pageKey)
    expect(agentPages?.pages[0]?.items.find((item) => item.id === 'conv-1')?.is_pinned).toBe(true)
    // infinite 캐시를 통째로 갈아끼우면 pageParams가 사라져 다음 fetchNextPage가 깨진다
    expect(agentPages?.pageParams).toEqual([undefined])
    expect(agentPages?.pages[0]?.next_cursor).toBe('cursor-next')

    const globalPages =
      queryClient.getQueryData<InfiniteData<ConversationWithAgentListEnvelope>>(globalKey)
    expect(globalPages?.pages[0]?.items.find((item) => item.id === 'conv-1')?.is_pinned).toBe(true)
    // 같은 페이지의 다른 대화는 건드리지 않는다
    expect(globalPages?.pages[0]?.items.find((item) => item.id === 'conv-2')?.is_pinned).toBe(false)

    // settle 후 내비게이터 캐시는 invalidate되어 서버 정렬 순서와 다시 동기화된다
    await waitFor(() => expect(queryClient.getQueryState(listKey)?.isInvalidated).toBe(true))
    expect(queryClient.getQueryState(globalKey)?.isInvalidated).toBe(true)
  })

  it('rolls back the optimistic patch when the update fails', async () => {
    const { Wrapper, queryClient } = createWrapperWithClient()
    seedNavigatorCaches(queryClient)
    let rejectUpdate: (error: Error) => void = () => {}
    apiMocks.update.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectUpdate = reject
        }),
    )

    const { result } = renderHook(
      () => useConversationRowActions({ activeConversationId: null }),
      { wrapper: Wrapper },
    )

    act(() => result.current.togglePin(mockConversation))

    // 낙관 패치가 먼저 적용된 것을 확인한 뒤 실패시킨다
    await waitFor(() => expect(pinnedInList(queryClient, 'conv-1')).toBe(true))
    act(() => rejectUpdate(new Error('network down')))

    await waitFor(() => expect(pinnedInList(queryClient, 'conv-1')).toBe(false))
    const agentPages = queryClient.getQueryData<InfiniteData<ConversationListEnvelope>>(pageKey)
    expect(agentPages?.pages[0]?.items.find((item) => item.id === 'conv-1')?.is_pinned).toBe(false)
    const globalPages =
      queryClient.getQueryData<InfiniteData<ConversationWithAgentListEnvelope>>(globalKey)
    expect(globalPages?.pages[0]?.items.find((item) => item.id === 'conv-1')?.is_pinned).toBe(false)
  })
})
