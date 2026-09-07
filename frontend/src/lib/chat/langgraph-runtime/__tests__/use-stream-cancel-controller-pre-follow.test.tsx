import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider, createStore } from 'jotai'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import type { ConversationRun, ConversationRunStatus } from '@/lib/types'
import { useStreamCancelController } from '../use-stream-cancel-controller'

vi.mock('@/lib/api/conversation-runs', () => ({
  conversationRunsApi: { active: vi.fn(), cancel: vi.fn(), get: vi.fn() },
}))

function deferred<T>() {
  let resolve: (value: T) => void = () => {}
  const promise = new Promise<T>((complete) => {
    resolve = complete
  })
  return { promise, resolve }
}

function run(status: ConversationRunStatus, id: string): ConversationRun {
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
  const lifetime = {}
  const lifetimeRef = { current: lifetime }
  const notices = vi.fn()
  const wrapper = ({ children }: { readonly children: ReactNode }) => (
    <Provider store={createStore()}>
      <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
    </Provider>
  )
  const hook = renderHook(
    () =>
      useStreamCancelController({
        conversationId: 'conversation',
        stream: { stop: vi.fn() },
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
        setChatCancelInFlight: vi.fn(),
      }),
    { wrapper },
  )
  return { ...hook, lifetimeRef, notices }
}

describe('useStreamCancelController pre-follow ownership', () => {
  beforeEach(() => {
    vi.mocked(conversationRunsApi.active).mockReset()
    vi.mocked(conversationRunsApi.cancel).mockReset()
    vi.mocked(conversationRunsApi.get).mockReset()
  })

  it('does not start a follower when unmounted during active lookup', async () => {
    const active = deferred<ConversationRun | null>()
    vi.mocked(conversationRunsApi.active).mockReturnValueOnce(active.promise)
    vi.mocked(conversationRunsApi.cancel).mockResolvedValueOnce(run('canceling', 'run-a'))
    const { result, unmount } = renderController()

    const cancel = result.current()
    await waitFor(() => expect(conversationRunsApi.active).toHaveBeenCalledOnce())
    unmount()
    active.resolve(run('running', 'run-a'))
    await cancel

    expect(conversationRunsApi.cancel).not.toHaveBeenCalled()
    expect(conversationRunsApi.get).not.toHaveBeenCalled()
  })

  it('does not start a follower when navigation changes lifetime during cancel request', async () => {
    const canceled = deferred<ConversationRun>()
    vi.mocked(conversationRunsApi.active).mockResolvedValueOnce(run('running', 'run-a'))
    vi.mocked(conversationRunsApi.cancel).mockReturnValueOnce(canceled.promise)
    const { lifetimeRef, notices, result } = renderController()

    const cancel = result.current()
    await waitFor(() => expect(conversationRunsApi.cancel).toHaveBeenCalledOnce())
    lifetimeRef.current = {}
    canceled.resolve(run('canceling', 'run-a'))
    await cancel

    expect(conversationRunsApi.get).not.toHaveBeenCalled()
    expect(notices).not.toHaveBeenCalledWith({ id: 'run-a', status: 'canceling' })
  })

  it('keeps the newer follower when the older active lookup resolves late', async () => {
    const activeA = deferred<ConversationRun | null>()
    const terminalB = deferred<ConversationRun>()
    vi.mocked(conversationRunsApi.active)
      .mockReturnValueOnce(activeA.promise)
      .mockResolvedValueOnce(run('running', 'run-b'))
    vi.mocked(conversationRunsApi.cancel)
      .mockResolvedValueOnce(run('canceling', 'run-b'))
      .mockResolvedValueOnce(run('canceling', 'run-a'))
    vi.mocked(conversationRunsApi.get).mockImplementation((_conversationId, runId) =>
      runId === 'run-b' ? terminalB.promise : Promise.resolve(run('canceled', 'run-a')),
    )
    const { notices, result } = renderController()

    const cancelA = result.current()
    await waitFor(() => expect(conversationRunsApi.active).toHaveBeenCalledOnce())
    await act(async () => result.current())
    activeA.resolve(run('running', 'run-a'))
    await cancelA

    expect(conversationRunsApi.get).toHaveBeenCalledTimes(1)
    terminalB.resolve(run('canceled', 'run-b'))
    await waitFor(() => {
      expect(notices).toHaveBeenLastCalledWith({ id: 'run-b', status: 'canceled' })
    })
    expect(notices).not.toHaveBeenCalledWith({ id: 'run-a', status: 'canceled' })
  })

  it('keeps the newer follower when the older cancel request resolves late', async () => {
    const cancelAResponse = deferred<ConversationRun>()
    const terminalB = deferred<ConversationRun>()
    vi.mocked(conversationRunsApi.active)
      .mockResolvedValueOnce(run('running', 'run-a'))
      .mockResolvedValueOnce(run('running', 'run-b'))
    vi.mocked(conversationRunsApi.cancel).mockImplementation((_conversationId, runId) =>
      runId === 'run-a' ? cancelAResponse.promise : Promise.resolve(run('canceling', 'run-b')),
    )
    vi.mocked(conversationRunsApi.get).mockImplementation((_conversationId, runId) =>
      runId === 'run-b' ? terminalB.promise : Promise.resolve(run('canceled', 'run-a')),
    )
    const { notices, result } = renderController()

    const cancelA = result.current()
    await waitFor(() =>
      expect(conversationRunsApi.cancel).toHaveBeenCalledWith('conversation', 'run-a'),
    )
    await act(async () => result.current())
    cancelAResponse.resolve(run('canceling', 'run-a'))
    await cancelA

    expect(conversationRunsApi.get).toHaveBeenCalledTimes(1)
    terminalB.resolve(run('canceled', 'run-b'))
    await waitFor(() => {
      expect(notices).toHaveBeenLastCalledWith({ id: 'run-b', status: 'canceled' })
    })
    expect(notices).not.toHaveBeenCalledWith({ id: 'run-a', status: 'canceled' })
  })
})
