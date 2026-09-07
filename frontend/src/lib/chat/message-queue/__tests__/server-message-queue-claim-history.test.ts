import type { AppendMessage } from '@assistant-ui/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createServerMessageQueue,
  type QueueRunStartAcceptance,
  type ServerMessageQueueApi,
} from '../server-message-queue'
import { message, queuedInput, queueList } from './server-message-queue-test-fixtures'

describe('createServerMessageQueue claimed run history', () => {
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

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('follows a claimed accepted input once without submitting it again', async () => {
    // Given an accepted pending input that is later claimed by a run.
    const pending = queuedInput()
    const claimed = queuedInput({ status: 'claimed', run_id: 'run-1', revision: 2 })
    submit.mockResolvedValue({
      inputId: pending.id,
      inputStatus: 'pending',
      revision: 1,
      position: 1,
    })
    vi.mocked(api.list)
      .mockResolvedValueOnce(queueList([pending]))
      .mockResolvedValue(queueList([claimed]))
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'request-1',
      onClaimedRun,
    })
    await queue.enqueue(message('queued'))

    // When duplicate reload/lifecycle reconciliation observes the same claim.
    await queue.refresh()
    await queue.refresh()

    // Then the run is followed once and no second run.start is sent.
    expect(submit).toHaveBeenCalledOnce()
    expect(onClaimedRun).toHaveBeenCalledOnce()
    expect(onClaimedRun).toHaveBeenCalledWith('run-1')
    expect(queue.getSnapshot().lastOperation).toMatchObject({ kind: 'applied', runId: 'run-1' })
  })

  it('follows only the newest claimed run when a steer claim precedes its canceled predecessor', async () => {
    // Given the authoritative priority ordering after an interrupt has claimed its successor.
    const successor = queuedInput({
      id: 'input-successor',
      run_id: 'run-successor',
      client_request_id: 'request-successor',
      status: 'claimed',
      priority: 100,
      position: 2,
      revision: 2,
      claimed_at: '2026-09-06T00:00:02Z',
    })
    const canceledPredecessor = queuedInput({
      id: 'input-predecessor',
      run_id: 'run-predecessor',
      client_request_id: 'request-predecessor',
      status: 'claimed',
      priority: 0,
      position: 1,
      revision: 2,
      claimed_at: '2026-09-06T00:00:01Z',
    })
    vi.mocked(api.list).mockResolvedValue(queueList([successor, canceledPredecessor]))
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'request-unused',
      onClaimedRun,
    })

    // When a fresh queue controller reconciles the server list.
    await queue.refresh()

    // Then the stale predecessor must not replace the successor follow selected by server order.
    expect(onClaimedRun).toHaveBeenCalledExactlyOnceWith('run-successor')
  })

  it('does not follow an older claim after the accepted successor was already followed directly', async () => {
    const successor = queuedInput({
      id: 'input-successor',
      run_id: 'run-successor',
      client_request_id: 'request-successor',
      status: 'claimed',
      priority: 100,
      claimed_at: '2026-09-06T00:00:02Z',
    })
    const predecessor = queuedInput({
      id: 'input-predecessor',
      run_id: 'run-predecessor',
      client_request_id: 'request-predecessor',
      status: 'claimed',
      claimed_at: '2026-09-06T00:00:01Z',
    })
    submit.mockResolvedValue({
      inputId: successor.id,
      inputStatus: 'claimed',
      revision: successor.revision,
      position: successor.position,
      runId: 'run-successor',
    })
    vi.mocked(api.list).mockResolvedValue(queueList([successor, predecessor]))
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'request-successor',
      onClaimedRun,
    })

    await queue.steer(message('explicit steer message'))

    expect(onClaimedRun).toHaveBeenCalledExactlyOnceWith('run-successor')
  })

  it('does not follow a delayed older claim after observing a newer claim', async () => {
    const successor = queuedInput({
      id: 'input-successor',
      run_id: 'run-successor',
      status: 'claimed',
      priority: 100,
      claimed_at: '2026-09-06T00:00:02Z',
    })
    const predecessor = queuedInput({
      id: 'input-predecessor',
      run_id: 'run-predecessor',
      status: 'claimed',
      claimed_at: '2026-09-06T00:00:01Z',
    })
    vi.mocked(api.list)
      .mockResolvedValueOnce(queueList([successor]))
      .mockResolvedValueOnce(queueList([predecessor]))
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'request-unused',
      onClaimedRun,
    })

    await queue.refresh()
    await queue.refresh()

    expect(onClaimedRun).toHaveBeenCalledExactlyOnceWith('run-successor')
  })

  it('prefers the exact accepted input correlation over claimed timestamp ordering', async () => {
    const accepted = queuedInput({
      id: 'input-accepted',
      run_id: 'run-accepted',
      client_request_id: 'request-accepted',
      status: 'claimed',
      claimed_at: '2026-09-06T00:00:01Z',
    })
    const unrelated = queuedInput({
      id: 'input-unrelated',
      run_id: 'run-unrelated',
      client_request_id: 'request-unrelated',
      status: 'claimed',
      claimed_at: '2026-09-06T00:00:02Z',
    })
    submit.mockResolvedValue({
      inputId: accepted.id,
      inputStatus: 'pending',
      revision: accepted.revision,
      position: accepted.position,
    })
    vi.mocked(api.list).mockResolvedValue(queueList([unrelated, accepted]))
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'request-accepted',
      onClaimedRun,
    })

    await queue.enqueue(message('accepted input'))

    expect(onClaimedRun).toHaveBeenCalledExactlyOnceWith('run-accepted')
  })

  it.each([
    ['missing', null, '2026-09-06T00:00:01Z'],
    ['invalid', 'not-a-timestamp', '2026-09-06T00:00:01Z'],
    ['tied', '2026-09-06T00:00:01Z', '2026-09-06T00:00:01Z'],
  ] as const)('does not guess between %s claimed timestamps', async (_label, firstAt, secondAt) => {
    vi.mocked(api.list).mockResolvedValue(
      queueList([
        queuedInput({
          id: 'input-first',
          run_id: 'run-first',
          status: 'claimed',
          claimed_at: firstAt,
        }),
        queuedInput({
          id: 'input-second',
          run_id: 'run-second',
          status: 'claimed',
          claimed_at: secondAt,
        }),
      ]),
    )
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'request-unused',
      onClaimedRun,
    })

    await queue.refresh()

    expect(onClaimedRun).not.toHaveBeenCalled()
  })
})
