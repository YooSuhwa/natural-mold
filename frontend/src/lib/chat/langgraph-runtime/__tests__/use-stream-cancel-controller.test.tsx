import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createStore, Provider } from 'jotai'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import { conversationRuntimeStatusAtom } from '@/lib/stores/chat-navigator-store'
import type { ConversationRun, ConversationRunStatus, MessagesEnvelope } from '@/lib/types'
import { useStreamCancelController } from '../use-stream-cancel-controller'

vi.mock('@/lib/api/conversation-runs', () => ({
  conversationRunsApi: {
    active: vi.fn(),
    cancel: vi.fn(),
    get: vi.fn(),
  },
}))

function createWrapper(queryClient: QueryClient, store: ReturnType<typeof createStore>) {
  return function Wrapper({ children }: { readonly children: ReactNode }) {
    return (
      <Provider store={store}>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </Provider>
    )
  }
}

function conversationRun(
  status: ConversationRunStatus,
  overrides: Partial<ConversationRun> = {},
): ConversationRun {
  return {
    id: 'run-1',
    conversation_id: 'conversation',
    agent_id: 'agent-1',
    parent_run_id: null,
    status,
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
    created_at: '2026-09-07T00:00:00Z',
    updated_at: '2026-09-07T00:00:00Z',
    metrics: null,
    ...overrides,
  }
}

function renderCancelController() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const store = createStore()
  store.set(conversationRuntimeStatusAtom, { conversation: 'running' })
  const lifetime = {}
  const notices = vi.fn()
  const acceptPendingSubmit = vi.fn(() => true)
  const stop = vi.fn()
  const stream = { stop }
  const { result } = renderHook(
    () =>
      useStreamCancelController({
        conversationId: 'conversation',
        stream,
        reconciliation: {
          lifetime,
          lifetimeRef: { current: lifetime },
          setThreadRunNotice: notices,
          clearPendingEdit: vi.fn(),
          clearPendingReload: vi.fn(),
          cancelPostRunHydration: vi.fn(),
          getPendingEditAttemptId: () => null,
          getPendingReloadAttemptId: () => null,
        },
        acceptPendingSubmit,
        setChatCancelInFlight: vi.fn(),
      }),
    { wrapper: createWrapper(queryClient, store) },
  )
  return { result, notices, acceptPendingSubmit, stop, store, queryClient }
}

describe('useStreamCancelController', () => {
  afterEach(() => vi.useRealTimers())

  beforeEach(() => {
    vi.mocked(conversationRunsApi.active).mockReset()
    vi.mocked(conversationRunsApi.cancel).mockReset()
    vi.mocked(conversationRunsApi.get).mockReset()
  })

  it('does not confirm cancellation when the active-run lookup fails', async () => {
    const lookupFailure = new Error('active lookup failed')
    vi.mocked(conversationRunsApi.active).mockRejectedValueOnce(lookupFailure)
    const { result, notices, store } = renderCancelController()

    await expect(result.current()).rejects.toBe(lookupFailure)

    expect(notices).not.toHaveBeenCalledWith(expect.objectContaining({ status: 'canceled' }))
    expect(store.get(conversationRuntimeStatusAtom).conversation).toBe('running')
  })

  it('settles with the exact failed terminal without fabricating canceled', async () => {
    vi.mocked(conversationRunsApi.active).mockResolvedValueOnce(conversationRun('running'))
    vi.mocked(conversationRunsApi.cancel).mockResolvedValueOnce(conversationRun('canceling'))
    vi.mocked(conversationRunsApi.get).mockResolvedValueOnce(
      conversationRun('failed', { error_message: 'terminal failure' }),
    )
    const { result, notices, store } = renderCancelController()

    await act(async () => result.current())

    expect(notices).toHaveBeenLastCalledWith({
      id: 'run-1',
      status: 'failed',
      errorMessage: 'terminal failure',
    })
    expect(notices).not.toHaveBeenCalledWith(expect.objectContaining({ status: 'canceled' }))
    expect(store.get(conversationRuntimeStatusAtom).conversation).toBe('idle')
  })

  it('correlates the pending submit with the exact active run before cancellation', async () => {
    vi.mocked(conversationRunsApi.active).mockResolvedValueOnce(conversationRun('running'))
    vi.mocked(conversationRunsApi.cancel).mockResolvedValueOnce(conversationRun('canceled'))
    const { result, acceptPendingSubmit } = renderCancelController()

    await act(async () => result.current())

    expect(acceptPendingSubmit).toHaveBeenCalledExactlyOnceWith('run-1')
  })

  it('publishes the exact terminal into the active messages envelope cache', async () => {
    vi.mocked(conversationRunsApi.active).mockResolvedValueOnce(conversationRun('running'))
    const canceled = conversationRun('canceled')
    vi.mocked(conversationRunsApi.cancel).mockResolvedValueOnce(canceled)
    const { result, queryClient } = renderCancelController()
    queryClient.setQueryData(['conversations', 'conversation', 'messages'], {
      messages: [],
      active_run: conversationRun('running'),
      latest_run: conversationRun('completed', { id: 'previous-run' }),
      total_estimated_cost: 0,
    })

    await act(async () => result.current())

    expect(
      queryClient.getQueryData<MessagesEnvelope>(['conversations', 'conversation', 'messages']),
    ).toEqual(
      expect.objectContaining({
        active_run: null,
        latest_run: canceled,
      }),
    )
  })

  it('settles an exact completed terminal without a cancellation notice', async () => {
    vi.mocked(conversationRunsApi.active).mockResolvedValueOnce(conversationRun('running'))
    vi.mocked(conversationRunsApi.cancel).mockResolvedValueOnce(conversationRun('canceling'))
    vi.mocked(conversationRunsApi.get).mockResolvedValueOnce(conversationRun('completed'))
    const { result, notices, store } = renderCancelController()

    await act(async () => result.current())

    expect(notices).toHaveBeenLastCalledWith(null)
    expect(notices).not.toHaveBeenCalledWith(expect.objectContaining({ status: 'canceled' }))
    expect(store.get(conversationRuntimeStatusAtom).conversation).toBe('idle')
  })

  it('retries an ID mismatch and never attributes its terminal status to the accepted run', async () => {
    vi.useFakeTimers()
    vi.mocked(conversationRunsApi.active).mockResolvedValueOnce(conversationRun('running'))
    vi.mocked(conversationRunsApi.cancel).mockResolvedValueOnce(conversationRun('canceling'))
    vi.mocked(conversationRunsApi.get)
      .mockResolvedValueOnce(conversationRun('canceled', { id: 'other-run' }))
      .mockResolvedValueOnce(conversationRun('completed'))
    const { result, notices, store } = renderCancelController()

    await act(async () => result.current())
    await act(async () => vi.advanceTimersByTimeAsync(1_000))

    expect(notices).not.toHaveBeenCalledWith(expect.objectContaining({ status: 'canceled' }))
    expect(notices).toHaveBeenLastCalledWith(null)
    expect(store.get(conversationRuntimeStatusAtom).conversation).toBe('idle')
  })

  it('recovers from a temporary exact-read rejection and later settles canceled', async () => {
    vi.useFakeTimers()
    vi.mocked(conversationRunsApi.active).mockResolvedValueOnce(conversationRun('running'))
    vi.mocked(conversationRunsApi.cancel).mockResolvedValueOnce(conversationRun('canceling'))
    vi.mocked(conversationRunsApi.get)
      .mockRejectedValueOnce(new Error('temporary exact read failure'))
      .mockResolvedValueOnce(conversationRun('canceled'))
    const { result, notices, store } = renderCancelController()

    await act(async () => result.current())
    await act(async () => vi.advanceTimersByTimeAsync(1_000))

    expect(notices).toHaveBeenLastCalledWith({ id: 'run-1', status: 'canceled' })
    expect(store.get(conversationRuntimeStatusAtom).conversation).toBe('idle')
  })

  it('confirms the exact accepted run when it becomes canceled after the cancel response', async () => {
    vi.mocked(conversationRunsApi.active).mockResolvedValueOnce(conversationRun('running'))
    vi.mocked(conversationRunsApi.cancel).mockResolvedValueOnce(conversationRun('canceling'))
    vi.mocked(conversationRunsApi.get).mockResolvedValueOnce(conversationRun('canceled'))
    const { result, notices, store } = renderCancelController()

    await act(async () => result.current())

    await waitFor(() => {
      expect(notices).toHaveBeenNthCalledWith(2, { id: 'run-1', status: 'canceled' })
    })
    expect(conversationRunsApi.get).toHaveBeenCalledExactlyOnceWith(
      'conversation',
      'run-1',
      expect.any(AbortSignal),
    )
    expect(store.get(conversationRuntimeStatusAtom).conversation).toBe('idle')
  })

  it('confirms cancellation only after the server returns canceled', async () => {
    vi.mocked(conversationRunsApi.active).mockResolvedValueOnce(conversationRun('running'))
    vi.mocked(conversationRunsApi.cancel).mockResolvedValueOnce(conversationRun('canceled'))
    const { result, notices, store } = renderCancelController()

    await act(async () => result.current())

    expect(notices).toHaveBeenCalledWith({ id: 'run-1', status: 'canceled' })
    expect(store.get(conversationRuntimeStatusAtom).conversation).toBe('idle')
  })
})
