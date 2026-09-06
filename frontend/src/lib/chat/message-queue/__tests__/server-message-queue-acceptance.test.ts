import type { AppendMessage } from '@assistant-ui/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createServerMessageQueue,
  type QueueRunStartAcceptance,
  type ServerMessageQueueApi,
} from '../server-message-queue'
import { message, queuedInput, queueList } from './server-message-queue-test-fixtures'

describe('createServerMessageQueue acceptance', () => {
  const submit =
    vi.fn<
      (
        message: AppendMessage,
        options: { strategy: 'enqueue' | 'interrupt'; requestId: string },
      ) => Promise<QueueRunStartAcceptance>
    >()
  const api: ServerMessageQueueApi = {
    list: vi.fn(),
    edit: vi.fn(),
    remove: vi.fn(),
    reorder: vi.fn(),
    promote: vi.fn(),
    resume: vi.fn(),
  }
  const onClaimedRun = vi.fn()
  let requestNumber = 0

  beforeEach(() => {
    vi.clearAllMocks()
    requestNumber = 0
  })

  it('persists a normal busy send once and exposes only accepted server queue state', async () => {
    // Given a pending server acceptance with attachment and context-bearing source content.
    const pending = queuedInput()
    submit.mockResolvedValue({
      inputId: pending.id,
      inputStatus: 'pending',
      revision: 1,
      position: 1,
    })
    vi.mocked(api.list).mockResolvedValue(queueList([pending]))
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => `request-${++requestNumber}`,
      onClaimedRun,
    })

    // When ordinary Enter is represented by an explicit queue submission.
    await queue.enqueue(message('queued', ['attachment-1']))

    // Then it is persisted immediately once and the official adapter reflects server state.
    expect(submit).toHaveBeenCalledOnce()
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ attachments: expect.any(Array) }),
      { strategy: 'enqueue', requestId: 'request-1' },
    )
    expect(queue.adapter.items).toEqual([
      expect.objectContaining({ id: 'input-1', prompt: 'queued' }),
    ])
    expect(queue.getSnapshot().lastOperation).toMatchObject({ kind: 'queued', inputId: 'input-1' })
  })

  it('surfaces rejection without an optimistic queued or applied item', async () => {
    // Given a server rejection.
    submit.mockRejectedValue(new Error('queue rejected'))
    vi.mocked(api.list).mockResolvedValue(queueList([]))
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'request-rejected',
      onClaimedRun,
    })

    // When a normal message is submitted.
    await queue.enqueue(message('rejected'))

    // Then no queue item is invented and failure remains visible.
    expect(queue.adapter.items).toEqual([])
    expect(queue.getSnapshot().lastOperation).toEqual({
      kind: 'failed',
      requestId: 'request-rejected',
      message: 'queue rejected',
    })
    expect(queue.getSnapshot()).toMatchObject({
      rejectedSubmission: {
        message: expect.objectContaining({
          content: [{ type: 'text', text: 'rejected' }],
        }),
        strategy: 'enqueue',
      },
    })
    expect(submit).toHaveBeenCalledOnce()
  })

  it('keeps an accepted queue result when follow-up reconciliation fails', async () => {
    const pending = queuedInput()
    submit.mockResolvedValue({
      inputId: pending.id,
      inputStatus: 'pending',
      revision: 1,
      position: 1,
    })
    vi.mocked(api.list).mockRejectedValue(new Error('reload unavailable'))
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'request-accepted',
      onClaimedRun,
    })

    await queue.enqueue(message('accepted'))

    expect(queue.getSnapshot()).toMatchObject({
      lastOperation: {
        kind: 'queued',
        requestId: 'request-accepted',
        inputId: 'input-1',
      },
      reconciliationError: 'reload unavailable',
    })
  })

  it('reconciles an uncertain submit failure by client request id without restoring or resending', async () => {
    const accepted = queuedInput({
      status: 'claimed',
      run_id: 'run-reconciled',
      revision: 2,
      client_request_id: 'request-uncertain',
    })
    submit.mockRejectedValue(new TypeError('network response lost'))
    vi.mocked(api.list).mockResolvedValue(queueList([accepted]))
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'request-uncertain',
      onClaimedRun,
    })

    await queue.enqueue(message('accepted despite lost response', ['attachment-1']))

    expect(submit).toHaveBeenCalledOnce()
    expect(onClaimedRun).toHaveBeenCalledExactlyOnceWith('run-reconciled')
    expect(queue.getSnapshot()).toMatchObject({
      lastOperation: {
        kind: 'applied',
        requestId: 'request-uncertain',
        inputId: 'input-1',
        runId: 'run-reconciled',
      },
      rejectedSubmission: null,
    })
  })

  it('reconciles one intentional retry by its exact request id without submitting', async () => {
    const claimed = queuedInput({
      id: 'input-retry',
      client_request_id: 'retry-request-1',
      status: 'claimed',
      run_id: 'run-retry',
    })
    vi.mocked(api.list).mockResolvedValue(queueList([claimed]))
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'unused',
      onClaimedRun,
    })

    await expect(queue.reconcileRequest('retry-request-1')).resolves.toEqual(claimed)
    expect(api.list).toHaveBeenCalledExactlyOnceWith('conversation-1')
    expect(submit).not.toHaveBeenCalled()
    expect(onClaimedRun).toHaveBeenCalledExactlyOnceWith('run-retry')
  })

  it('keeps the external-store snapshot referentially stable between changes', async () => {
    vi.mocked(api.list).mockResolvedValue(queueList([]))
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'unused',
      onClaimedRun,
    })

    const initial = queue.getSnapshot()
    expect(queue.getSnapshot()).toBe(initial)

    await queue.refresh()
    expect(queue.getSnapshot()).not.toBe(initial)
    expect(queue.getSnapshot()).toBe(queue.getSnapshot())
  })
})
