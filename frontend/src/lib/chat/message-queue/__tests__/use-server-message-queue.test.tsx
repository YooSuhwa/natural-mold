import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  edit: vi.fn(),
  remove: vi.fn(),
  reorder: vi.fn(),
  promote: vi.fn(),
  resume: vi.fn(),
}))

vi.mock('@/lib/api/conversation-run-inputs', () => ({
  conversationRunInputsApi: mocks,
}))

import { useServerMessageQueue } from '../use-server-message-queue'

describe('useServerMessageQueue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.list.mockResolvedValue({ queue_paused: false, items: [] })
  })

  it('hydrates the authoritative queue on mount and exposes one stable controller', async () => {
    const submit = vi.fn()
    const onClaimedRun = vi.fn()
    const createRequestId = () => 'request-1'
    const { result, rerender } = renderHook(() =>
      useServerMessageQueue({
        conversationId: 'conversation-1',
        submit,
        onClaimedRun,
        createRequestId,
      }),
    )

    await waitFor(() => expect(mocks.list).toHaveBeenCalledWith('conversation-1'))
    const controller = result.current.controller
    rerender()

    expect(result.current.controller).toBe(controller)
    expect(result.current.snapshot).toMatchObject({ queuePaused: false, items: [] })
  })

  it('polls a pending server item until it is claimed, then follows the run once', async () => {
    const pending = {
      id: 'input-1',
      conversation_id: 'conversation-1',
      run_id: null,
      client_request_id: 'request-1',
      source: 'chat',
      status: 'pending',
      priority: 0,
      position: 1,
      revision: 1,
      input_payload: { messages: [{ role: 'user', content: 'queued' }] },
      attachment_ids: [],
      checkpoint_id: null,
      claimed_at: null,
      created_at: '2026-09-06T00:00:00',
      updated_at: '2026-09-06T00:00:00',
    } as const
    mocks.list.mockResolvedValueOnce({ queue_paused: false, items: [pending] }).mockResolvedValue({
      queue_paused: false,
      items: [{ ...pending, status: 'claimed', run_id: 'run-1', revision: 2 }],
    })
    const onClaimedRun = vi.fn()
    const submit = vi.fn()
    const createRequestId = () => 'request-1'
    const { unmount } = renderHook(() =>
      useServerMessageQueue({
        conversationId: 'conversation-1',
        submit,
        onClaimedRun,
        createRequestId,
        pollIntervalMs: 10,
      }),
    )

    await waitFor(() => expect(onClaimedRun).toHaveBeenCalledExactlyOnceWith('run-1'))
    unmount()
  })

  it('keeps polling an accepted input when its first reconciliation reload fails', async () => {
    const pending = {
      id: 'input-accepted',
      conversation_id: 'conversation-1',
      run_id: null,
      client_request_id: 'request-accepted',
      source: 'chat',
      status: 'pending' as const,
      priority: 0,
      position: 1,
      revision: 1,
      input_payload: { messages: [{ role: 'user', content: 'accepted' }] },
      attachment_ids: [],
      checkpoint_id: null,
      claimed_at: null,
      created_at: '2026-09-06T00:00:00',
      updated_at: '2026-09-06T00:00:00',
    } as const
    mocks.list
      .mockResolvedValueOnce({ queue_paused: false, items: [] })
      .mockRejectedValueOnce(new Error('reload unavailable'))
      .mockResolvedValue({
        queue_paused: false,
        items: [{ ...pending, status: 'claimed', run_id: 'run-accepted', revision: 2 }],
      })
    const submit = vi.fn().mockResolvedValue({
      inputId: pending.id,
      inputStatus: 'pending',
      revision: 1,
      position: 1,
    })
    const onClaimedRun = vi.fn()
    const createRequestId = () => 'request-accepted'
    const { result, unmount } = renderHook(() =>
      useServerMessageQueue({
        conversationId: 'conversation-1',
        submit,
        onClaimedRun,
        createRequestId,
        pollIntervalMs: 10,
      }),
    )

    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(1))
    await act(async () => {
      await result.current.controller.enqueue({
        role: 'user',
        content: [{ type: 'text', text: 'accepted' }],
        attachments: [],
        createdAt: new Date('2026-09-06T00:00:00Z'),
        parentId: null,
        sourceId: null,
        runConfig: {},
        metadata: { custom: {} },
      })
    })

    await waitFor(() => expect(onClaimedRun).toHaveBeenCalledExactlyOnceWith('run-accepted'))
    expect(submit).toHaveBeenCalledOnce()
    expect(result.current.snapshot.lastOperation).toMatchObject({
      kind: 'applied',
      inputId: 'input-accepted',
      runId: 'run-accepted',
    })
    unmount()
  })
})
