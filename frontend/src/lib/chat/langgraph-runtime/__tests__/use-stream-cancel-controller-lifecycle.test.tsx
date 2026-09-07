import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createStore, Provider } from 'jotai'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import type { ConversationRun, ConversationRunStatus } from '@/lib/types'
import { useStreamCancelController } from '../use-stream-cancel-controller'

vi.mock('@/lib/api/conversation-runs', () => ({
  conversationRunsApi: { active: vi.fn(), cancel: vi.fn(), get: vi.fn() },
}))

function run(status: ConversationRunStatus, id = 'run-1'): ConversationRun {
  return {
    id,
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
  }
}

function renderController() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const store = createStore()
  const lifetime = {}
  const lifetimeRef = { current: lifetime }
  const notices = vi.fn()
  const stop = vi.fn()
  const setChatCancelInFlight = vi.fn()
  const wrapper = ({ children }: { readonly children: ReactNode }) => (
    <Provider store={store}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </Provider>
  )
  const { result, unmount } = renderHook(
    () =>
      useStreamCancelController({
        conversationId: 'conversation',
        stream: { stop },
        reconciliation: {
          lifetime,
          lifetimeRef,
          setThreadRunNotice: notices,
          clearPendingEdit: vi.fn(),
          clearPendingReload: vi.fn(),
          cancelPostRunHydration: vi.fn(),
          getPendingEditAttemptId: () => null,
          getPendingReloadAttemptId: () => null,
        },
        pendingSubmit: null,
        clearPendingSubmit: vi.fn(),
        setChatCancelInFlight,
      }),
    { wrapper },
  )
  return { lifetimeRef, notices, result, setChatCancelInFlight, stop, unmount }
}

describe('useStreamCancelController lifecycle', () => {
  beforeEach(() => {
    vi.mocked(conversationRunsApi.active).mockReset()
    vi.mocked(conversationRunsApi.cancel).mockReset()
    vi.mocked(conversationRunsApi.get).mockReset()
  })

  it('aborts the exact-run follow on unmount and releases the cancel latch', async () => {
    let followSignal: AbortSignal | undefined
    vi.mocked(conversationRunsApi.active).mockResolvedValueOnce(run('running'))
    vi.mocked(conversationRunsApi.cancel).mockResolvedValueOnce(run('canceling'))
    vi.mocked(conversationRunsApi.get).mockImplementationOnce((_conversationId, _runId, signal) => {
      followSignal = signal
      return new Promise(() => {})
    })
    const { result, setChatCancelInFlight, unmount } = renderController()

    await act(async () => result.current())
    unmount()

    expect(followSignal?.aborted).toBe(true)
    expect(setChatCancelInFlight).toHaveBeenLastCalledWith(false)
  })

  it('aborts the prior follow when a newer cancel attempt owns reconciliation', async () => {
    let firstSignal: AbortSignal | undefined
    vi.mocked(conversationRunsApi.active)
      .mockResolvedValueOnce(run('running'))
      .mockResolvedValueOnce(run('running', 'run-2'))
    vi.mocked(conversationRunsApi.cancel)
      .mockResolvedValueOnce(run('canceling'))
      .mockResolvedValueOnce(run('canceling', 'run-2'))
    vi.mocked(conversationRunsApi.get)
      .mockImplementationOnce((_conversationId, _runId, signal) => {
        firstSignal = signal
        return new Promise(() => {})
      })
      .mockResolvedValueOnce(run('canceled', 'run-2'))
    const { notices, result } = renderController()

    await act(async () => result.current())
    await act(async () => result.current())
    await waitFor(() => {
      expect(notices).toHaveBeenLastCalledWith({ id: 'run-2', status: 'canceled' })
    })

    expect(firstSignal?.aborted).toBe(true)
    expect(notices).not.toHaveBeenCalledWith({ id: 'run-1', status: 'canceled' })
  })

  it('does not write a terminal notice after navigation changes the lifetime', async () => {
    let resolveRun: ((value: ConversationRun) => void) | undefined
    vi.mocked(conversationRunsApi.active).mockResolvedValueOnce(run('running'))
    vi.mocked(conversationRunsApi.cancel).mockResolvedValueOnce(run('canceling'))
    vi.mocked(conversationRunsApi.get).mockImplementationOnce(
      () => new Promise((resolve) => (resolveRun = resolve)),
    )
    const { lifetimeRef, notices, result } = renderController()

    await act(async () => result.current())
    lifetimeRef.current = {}
    await act(async () => resolveRun?.(run('canceled')))

    expect(notices).not.toHaveBeenCalledWith({ id: 'run-1', status: 'canceled' })
  })

  it('continues durable cancellation when local stream stop rejects', async () => {
    vi.mocked(conversationRunsApi.active).mockResolvedValueOnce(run('running'))
    vi.mocked(conversationRunsApi.cancel).mockResolvedValueOnce(run('canceling'))
    vi.mocked(conversationRunsApi.get).mockResolvedValueOnce(run('canceled'))
    const { notices, result, setChatCancelInFlight, stop } = renderController()
    stop.mockRejectedValueOnce(new Error('local stop rejected'))

    await act(async () => result.current())
    await waitFor(() => {
      expect(notices).toHaveBeenLastCalledWith({ id: 'run-1', status: 'canceled' })
    })

    expect(setChatCancelInFlight).toHaveBeenLastCalledWith(false)
  })
})
