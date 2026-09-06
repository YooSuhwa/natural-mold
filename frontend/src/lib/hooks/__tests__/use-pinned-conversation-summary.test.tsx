import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { pinnedConversationSummaryApi } from '@/lib/api/pinned-conversation-summary'
import {
  pinnedConversationSummaryKeys,
  usePinnedConversationSummary,
} from '@/lib/hooks/use-pinned-conversation-summary'
import type { PinnedConversationSummary } from '@/lib/types/pinned-conversation-summary'

vi.mock('@/lib/api/pinned-conversation-summary', () => ({
  pinnedConversationSummaryApi: {
    get: vi.fn(),
    pin: vi.fn(),
    unpin: vi.fn(),
  },
}))

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { readonly children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

const summary = {
  conversation_id: 'conversation-1',
  source_message_id: 'message-1',
  source_branch_checkpoint_id: 'checkpoint-1',
  snapshot_text: 'Persisted summary',
  source_status: 'current' as const,
  created_at: '2026-09-06T00:00:00Z',
  updated_at: '2026-09-06T00:00:00Z',
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

describe('usePinnedConversationSummary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('hydrates the server-persisted summary on mount', async () => {
    // Given
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    vi.mocked(pinnedConversationSummaryApi.get).mockResolvedValue({ summary })

    // When
    const { result } = renderHook(() => usePinnedConversationSummary('conversation-1'), {
      wrapper: createWrapper(queryClient),
    })

    // Then
    await waitFor(() => expect(result.current.summary).toEqual(summary))
    expect(pinnedConversationSummaryApi.get).toHaveBeenCalledWith('conversation-1')
  })

  it('replaces the cached snapshot only after the pin request succeeds', async () => {
    // Given
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    let serverSummary: PinnedConversationSummary | null = null
    vi.mocked(pinnedConversationSummaryApi.get).mockImplementation(async () => ({
      summary: serverSummary,
    }))
    vi.mocked(pinnedConversationSummaryApi.pin).mockImplementation(async () => {
      serverSummary = summary
      return summary
    })
    const { result } = renderHook(() => usePinnedConversationSummary('conversation-1'), {
      wrapper: createWrapper(queryClient),
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    // When
    act(() => result.current.pin('message-1'))

    // Then
    await waitFor(() =>
      expect(
        queryClient.getQueryData(pinnedConversationSummaryKeys.detail('conversation-1')),
      ).toEqual({ summary }),
    )
  })

  it('retains the current snapshot when unpin is rejected', async () => {
    // Given
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(pinnedConversationSummaryKeys.detail('conversation-1'), { summary })
    vi.mocked(pinnedConversationSummaryApi.get).mockResolvedValue({ summary })
    vi.mocked(pinnedConversationSummaryApi.unpin).mockRejectedValue(new Error('denied'))
    const { result } = renderHook(() => usePinnedConversationSummary('conversation-1'), {
      wrapper: createWrapper(queryClient),
    })

    // When
    act(() => result.current.unpin())

    // Then
    await waitFor(() => expect(pinnedConversationSummaryApi.unpin).toHaveBeenCalledOnce())
    expect(
      queryClient.getQueryData(pinnedConversationSummaryKeys.detail('conversation-1')),
    ).toEqual({ summary })
  })

  it('serializes independent consumers and converges from the authoritative final read', async () => {
    // Given
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const pendingPin = deferred<PinnedConversationSummary>()
    let serverSummary: PinnedConversationSummary | null = null
    vi.mocked(pinnedConversationSummaryApi.get).mockImplementation(async () => ({
      summary: serverSummary,
    }))
    vi.mocked(pinnedConversationSummaryApi.pin).mockImplementation(async () => {
      const pinned = await pendingPin.promise
      serverSummary = pinned
      return pinned
    })
    vi.mocked(pinnedConversationSummaryApi.unpin).mockImplementation(async () => {
      serverSummary = null
    })
    const { result } = renderHook(
      () => ({
        first: usePinnedConversationSummary('conversation-1'),
        second: usePinnedConversationSummary('conversation-1'),
      }),
      { wrapper: createWrapper(queryClient) },
    )
    await waitFor(() => expect(result.current.first.isLoading).toBe(false))

    // When: separate message/header consumers issue opposite mutations before pin resolves.
    act(() => {
      result.current.first.pin('message-1')
      result.current.second.unpin()
    })

    // Then: the shared scope queues unpin, and the final GET agrees with persisted state.
    await waitFor(() => expect(pinnedConversationSummaryApi.pin).toHaveBeenCalledOnce())
    expect(pinnedConversationSummaryApi.unpin).not.toHaveBeenCalled()
    expect(result.current.first.isMutating).toBe(true)
    expect(result.current.second.isMutating).toBe(true)
    act(() => pendingPin.resolve(summary))
    await waitFor(() => expect(pinnedConversationSummaryApi.unpin).toHaveBeenCalledOnce())
    await waitFor(() => {
      expect(result.current.first.isMutating).toBe(false)
      expect(
        queryClient.getQueryData(pinnedConversationSummaryKeys.detail('conversation-1')),
      ).toEqual({ summary: null })
    })
  })
})
