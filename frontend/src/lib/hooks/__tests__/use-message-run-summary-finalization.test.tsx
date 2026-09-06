import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useMessageRunSummary } from '@/lib/hooks/use-message-run-summary'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import { apiFetch } from '@/lib/api/client'
import type { ConversationRun } from '@/lib/types'

vi.mock('@/lib/api/conversation-runs', () => ({
  conversationRunsApi: { get: vi.fn() },
}))
vi.mock('@/lib/api/client', () => ({ apiFetch: vi.fn() }))

const RUN_ID = 'ed9e5844-0269-4b0f-9325-5aebe93ac1d2'

function completedRun(): ConversationRun {
  return {
    id: RUN_ID,
    conversation_id: 'conversation-1',
    agent_id: 'agent-1',
    parent_run_id: null,
    status: 'completed',
    source: 'chat',
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
    created_at: '2026-09-06T00:00:00Z',
    updated_at: '2026-09-06T00:00:02Z',
    metrics: null,
  }
}

function createWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useMessageRunSummary finalization', () => {
  beforeEach(() => {
    vi.mocked(conversationRunsApi.get).mockReset()
    vi.mocked(apiFetch).mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('resolves the same live message after its durable link is finalized without remounting', async () => {
    const messageId = 'lc_run--final'
    let finalized = false
    vi.mocked(apiFetch).mockImplementation(async () =>
      finalized ? [{ message_id: messageId, run_id: RUN_ID }] : [],
    )
    vi.mocked(conversationRunsApi.get).mockResolvedValue(completedRun())

    const { result, rerender } = renderHook(
      ({ isRunning }) => useMessageRunSummary('conversation-1', messageId, [messageId], isRunning),
      { initialProps: { isRunning: true }, wrapper: createWrapper() },
    )
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1))
    expect(result.current.summary).toBeNull()

    finalized = true
    rerender({ isRunning: false })

    await waitFor(() => expect(result.current.summary?.runId).toBe(RUN_ID))
    expect(apiFetch).toHaveBeenCalledTimes(2)
  })

  it('stops terminal finalization lookups immediately after the exact link resolves', async () => {
    vi.useFakeTimers()
    const messageId = 'lc_run--final'
    vi.mocked(apiFetch).mockResolvedValue([{ message_id: messageId, run_id: RUN_ID }])
    vi.mocked(conversationRunsApi.get).mockResolvedValue(completedRun())

    renderHook(() => useMessageRunSummary('conversation-1', messageId, [messageId], false), {
      wrapper: createWrapper(),
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000)
    })

    expect(apiFetch).toHaveBeenCalledTimes(1)
  })

  it('keeps a completed summary visible while a newer thread run is active', async () => {
    const completedMessageId = 'lc_run--completed'
    vi.mocked(apiFetch).mockResolvedValue([{ message_id: completedMessageId, run_id: RUN_ID }])
    vi.mocked(conversationRunsApi.get).mockResolvedValue(completedRun())

    const { result, rerender } = renderHook(
      ({ isRunning, visibleMessageIds }) =>
        useMessageRunSummary('conversation-1', completedMessageId, visibleMessageIds, isRunning),
      {
        initialProps: { isRunning: false, visibleMessageIds: [completedMessageId] },
        wrapper: createWrapper(),
      },
    )
    await waitFor(() => expect(result.current.summary?.runId).toBe(RUN_ID))

    let resolveLinks!: (links: { message_id: string; run_id: string }[]) => void
    vi.mocked(apiFetch).mockReturnValue(
      new Promise((resolve) => {
        resolveLinks = resolve
      }),
    )
    rerender({
      isRunning: true,
      visibleMessageIds: [completedMessageId, 'lc_run--new-live'],
    })

    expect(result.current.summary?.runId).toBe(RUN_ID)
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2))
    await act(async () => {
      resolveLinks([{ message_id: completedMessageId, run_id: RUN_ID }])
    })
    expect(result.current.summary?.runId).toBe(RUN_ID)
  })

  it('keeps a changed public id unknown while pending and after an authoritative omission', async () => {
    const rawMessageId = 'lc_run--reused'
    vi.mocked(apiFetch).mockResolvedValue([{ message_id: rawMessageId, run_id: RUN_ID }])
    vi.mocked(conversationRunsApi.get).mockResolvedValue(completedRun())

    const { result, rerender } = renderHook(
      ({ messageId, isRunning }) =>
        useMessageRunSummary('conversation-1', messageId, [rawMessageId], isRunning),
      {
        initialProps: { messageId: rawMessageId, isRunning: false },
        wrapper: createWrapper(),
      },
    )
    await waitFor(() => expect(result.current.summary?.runId).toBe(RUN_ID))

    let resolveLinks!: (links: { message_id: string; run_id: string }[]) => void
    vi.mocked(apiFetch).mockReturnValue(
      new Promise((resolve) => {
        resolveLinks = resolve
      }),
    )
    vi.mocked(conversationRunsApi.get).mockClear()
    rerender({ messageId: `${rawMessageId}::moldy-turn-2`, isRunning: true })

    expect(result.current.summary).toBeNull()
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2))
    await act(async () => {
      resolveLinks([])
    })
    await waitFor(() => expect(result.current.summary).toBeNull())
    expect(conversationRunsApi.get).not.toHaveBeenCalled()
  })

  it('stops terminal finalization lookups after the bounded empty-result allowance', async () => {
    vi.useFakeTimers()
    vi.mocked(apiFetch).mockResolvedValue([])

    renderHook(() => useMessageRunSummary('conversation-1', 'legacy-message-id', [], false), {
      wrapper: createWrapper(),
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000)
    })

    expect(apiFetch).toHaveBeenCalledTimes(4)
    expect(conversationRunsApi.get).not.toHaveBeenCalled()
  })

  it('counts failed HTTP lookups toward the same terminal bound', async () => {
    vi.useFakeTimers()
    vi.mocked(apiFetch).mockRejectedValue(new Error('temporary lookup failure'))

    renderHook(() => useMessageRunSummary('conversation-1', 'legacy-message-id', [], false), {
      wrapper: createWrapper(),
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000)
    })

    expect(apiFetch).toHaveBeenCalledTimes(4)
    expect(conversationRunsApi.get).not.toHaveBeenCalled()
  })

  it('starts a fresh bounded terminal budget when the same batch completes again', async () => {
    vi.useFakeTimers()
    const messageId = 'lc_run--final'
    let finalized = false
    vi.mocked(apiFetch).mockImplementation(async () =>
      finalized ? [{ message_id: messageId, run_id: RUN_ID }] : [],
    )
    vi.mocked(conversationRunsApi.get).mockResolvedValue(completedRun())

    const { rerender } = renderHook(
      ({ isRunning }) => useMessageRunSummary('conversation-1', messageId, [messageId], isRunning),
      { initialProps: { isRunning: false }, wrapper: createWrapper() },
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000)
    })
    expect(apiFetch).toHaveBeenCalledTimes(4)

    rerender({ isRunning: true })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(apiFetch).toHaveBeenCalledTimes(5)

    finalized = true
    rerender({ isRunning: false })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })

    expect(apiFetch).toHaveBeenCalledTimes(6)
  })

  it('never carries placeholder links into another conversation', async () => {
    const messageId = 'lc_run--final'
    vi.mocked(apiFetch).mockImplementation(async (path) =>
      path.includes('/conversation-a/') ? [{ message_id: messageId, run_id: RUN_ID }] : [],
    )
    vi.mocked(conversationRunsApi.get).mockResolvedValue(completedRun())

    const { result, rerender } = renderHook(
      ({ conversationId }) => useMessageRunSummary(conversationId, messageId, [messageId], false),
      { initialProps: { conversationId: 'conversation-a' }, wrapper: createWrapper() },
    )
    await waitFor(() => expect(result.current.summary?.runId).toBe(RUN_ID))

    rerender({ conversationId: 'conversation-b' })
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith(
        '/api/conversations/conversation-b/run-message-links?message_id=lc_run--final',
      ),
    )

    expect(conversationRunsApi.get).not.toHaveBeenCalledWith('conversation-b', RUN_ID)
  })
})
