import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMessageRunSummary } from '@/lib/hooks/use-message-run-summary'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import { apiFetch } from '@/lib/api/client'

vi.mock('@/lib/api/conversation-runs', () => ({
  conversationRunsApi: { get: vi.fn() },
}))
vi.mock('@/lib/api/client', () => ({ apiFetch: vi.fn() }))

const RUN_ID = 'ed9e5844-0269-4b0f-9325-5aebe93ac1d2'

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      {children}
    </QueryClientProvider>
  )
}

describe('useMessageRunSummary', () => {
  beforeEach(() => {
    vi.mocked(conversationRunsApi.get).mockReset()
    vi.mocked(apiFetch).mockReset()
  })

  it('loads the exact durable run attached to the assistant message id after reload', async () => {
    const messageId = '4722fbe8-b983-52ac-8ce0-9329a8f4d453'
    vi.mocked(apiFetch).mockResolvedValue([
      {
        message_id: messageId,
        run_id: RUN_ID,
      },
    ])
    vi.mocked(conversationRunsApi.get).mockResolvedValue({
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
    })

    const { result } = renderHook(() => useMessageRunSummary('conversation-1', messageId), {
      wrapper,
    })

    await waitFor(() => expect(result.current.summary?.runId).toBe(RUN_ID))
    expect(conversationRunsApi.get).toHaveBeenCalledWith('conversation-1', RUN_ID)
    expect(apiFetch).toHaveBeenCalledWith(
      `/api/conversations/conversation-1/run-message-links?message_id=${messageId}`,
    )
    expect(result.current.summary?.elapsedMs).toBeNull()
  })

  it('resolves the retained raw runtime id through its known duplicate-turn suffix', async () => {
    const rawMessageId = 'lc_run--final'
    const turnMessageId = `${rawMessageId}::moldy-turn-2`
    vi.mocked(apiFetch).mockResolvedValue([{ message_id: rawMessageId, run_id: RUN_ID }])
    vi.mocked(conversationRunsApi.get).mockResolvedValue({
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
    })

    const { result } = renderHook(
      () => useMessageRunSummary('conversation-1', turnMessageId, [rawMessageId, turnMessageId]),
      { wrapper },
    )

    await waitFor(() => expect(result.current.summary?.runId).toBe(RUN_ID))
    expect(apiFetch).toHaveBeenCalledWith(
      '/api/conversations/conversation-1/run-message-links?message_id=lc_run--final',
    )
  })

  it('keeps a legacy message unknown when the bounded resolver has no authoritative link', async () => {
    vi.mocked(apiFetch).mockResolvedValue([])
    const { result } = renderHook(
      () => useMessageRunSummary('conversation-1', 'legacy-message-id'),
      { wrapper },
    )

    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1))
    expect(result.current.summary).toBeNull()
    expect(conversationRunsApi.get).not.toHaveBeenCalled()
  })

  it('switches to the newly selected message run without leaking a canceled steer run', async () => {
    const oldRunId = 'ed9e5844-0269-4b0f-9325-5aebe93ac1d2'
    const newRunId = 'd2c94d2c-a116-40c3-ac2a-84d28946d70d'
    const oldMessageId = '4722fbe8-b983-52ac-8ce0-9329a8f4d453'
    const newMessageId = 'bd7d2ec5-224c-500f-bb41-46d7e1cc2023'
    vi.mocked(apiFetch).mockResolvedValue([
      {
        message_id: oldMessageId,
        run_id: oldRunId,
      },
      {
        message_id: newMessageId,
        run_id: newRunId,
      },
    ])
    vi.mocked(conversationRunsApi.get).mockImplementation(async (_conversationId, runId) => ({
      id: runId,
      conversation_id: 'conversation-1',
      agent_id: 'agent-1',
      parent_run_id: runId === newRunId ? oldRunId : null,
      status: runId === oldRunId ? 'canceled' : 'completed',
      source: runId === oldRunId ? 'chat' : 'edit',
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
      updated_at: '2026-09-06T00:00:03Z',
      metrics: null,
    }))

    const { result, rerender } = renderHook(
      ({ messageId }) =>
        useMessageRunSummary('conversation-1', messageId, [oldMessageId, newMessageId]),
      { initialProps: { messageId: oldMessageId }, wrapper },
    )
    await waitFor(() => expect(result.current.summary?.runId).toBe(oldRunId))

    rerender({ messageId: newMessageId })
    await waitFor(() => expect(result.current.summary?.runId).toBe(newRunId))
    expect(result.current.summary?.status).toBe('completed')
    expect(apiFetch).toHaveBeenCalledTimes(1)
  })

  it('loads a failed synthetic notice by its exact embedded run id', async () => {
    vi.mocked(conversationRunsApi.get).mockResolvedValue({
      id: RUN_ID,
      conversation_id: 'conversation-1',
      agent_id: 'agent-1',
      parent_run_id: null,
      status: 'failed',
      source: 'chat',
      worker_instance_id: null,
      interrupt_id: null,
      last_event_id: null,
      input_preview: null,
      error_code: 'MODEL_FAILED',
      error_message: 'failed',
      cancel_requested_at: null,
      started_at: null,
      heartbeat_at: null,
      completed_at: null,
      created_at: '2026-09-06T00:00:00Z',
      updated_at: '2026-09-06T00:00:02Z',
      metrics: null,
    })

    const { result } = renderHook(
      () => useMessageRunSummary('conversation-1', `moldy-failed-${RUN_ID}`),
      { wrapper },
    )

    await waitFor(() => expect(result.current.summary?.status).toBe('failed'))
    expect(apiFetch).not.toHaveBeenCalled()
    expect(conversationRunsApi.get).toHaveBeenCalledWith('conversation-1', RUN_ID)
  })
})
