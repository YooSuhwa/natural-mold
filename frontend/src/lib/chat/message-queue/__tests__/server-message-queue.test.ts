import type { AppendMessage } from '@assistant-ui/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createServerMessageQueue,
  type QueueRunStartAcceptance,
  type ServerMessageQueueApi,
} from '../server-message-queue'
import { message, queuedInput, queueList } from './server-message-queue-test-fixtures'

describe('createServerMessageQueue mutations', () => {
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

  it('edits, reorders, and removes pending items with their current server revisions', async () => {
    // Given two authoritative pending items.
    const first = queuedInput({
      resource_context: [
        {
          kind: 'artifact',
          id: '11111111-1111-4111-8111-111111111111',
          version_id: '22222222-2222-4222-8222-222222222222',
          label: 'Pinned artifact',
        },
      ],
      input_payload: {
        messages: [
          {
            role: 'user',
            content: 'queued',
            protocol_context: { opaque: true },
            metadata: { retained: 'server-value' },
          },
        ],
        context: { artifact_ids: ['artifact-1'] },
      },
    })
    const second = queuedInput({
      id: 'input-2',
      client_request_id: 'request-2',
      position: 2,
      input_payload: { messages: [{ role: 'user', content: 'second' }] },
    })
    vi.mocked(api.list).mockResolvedValue(queueList([first, second]))
    vi.mocked(api.edit).mockResolvedValue({ ...first, revision: 2 })
    vi.mocked(api.reorder).mockResolvedValue([
      { ...second, position: 1, revision: 2 },
      { ...first, position: 2, revision: 2 },
    ])
    vi.mocked(api.remove).mockResolvedValue({ ...first, status: 'canceled', revision: 3 })
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'unused',
      onClaimedRun,
    })
    await queue.refresh()

    // When the first item is edited, moved after the second, and removed.
    await queue.edit('input-1', message('edited', ['new-attachment-must-not-replace']))
    await queue.move('input-1', { lane: 'queue', insertAfter: 'input-2' })
    await queue.remove('input-1')

    // Then every mutation uses the revision returned by its predecessor and edit preserves attachments.
    expect(api.edit).toHaveBeenCalledWith('conversation-1', 'input-1', {
      expectedRevision: 1,
      input: {
        context: { artifact_ids: ['artifact-1'] },
        messages: [
          {
            role: 'user',
            content: [{ type: 'text', text: 'edited' }],
            protocol_context: { opaque: true },
            metadata: {
              retained: 'server-value',
              context: [{ kind: 'artifact', id: 'artifact-1' }],
            },
          },
        ],
        resource_context: [
          {
            kind: 'artifact',
            id: '11111111-1111-4111-8111-111111111111',
            version_id: '22222222-2222-4222-8222-222222222222',
            label: 'Pinned artifact',
          },
        ],
      },
    })
    expect(api.reorder).toHaveBeenCalledWith('conversation-1', [
      { id: 'input-2', revision: 1 },
      { id: 'input-1', revision: 2 },
    ])
    expect(api.remove).toHaveBeenCalledWith('conversation-1', 'input-1', 2)
  })

  it('uses interrupt only for explicit new steer and promotes an existing item by revision', async () => {
    // Given one existing ordinary pending item.
    const pending = queuedInput()
    vi.mocked(api.list).mockResolvedValue(queueList([pending]))
    vi.mocked(api.promote).mockResolvedValue({ ...pending, priority: 100, revision: 2 })
    submit.mockResolvedValue({
      inputId: 'input-new-steer',
      inputStatus: 'pending',
      revision: 1,
      position: 2,
    })
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'request-new-steer',
      onClaimedRun,
    })
    await queue.refresh()

    // When a new explicit steer and the official existing-item Steer action are used.
    await queue.steer(message('new steer'))
    await queue.move('input-1', { lane: 'steer', insertAfter: null })

    // Then the new input interrupts once, while the existing input is promoted without replay.
    expect(submit).toHaveBeenCalledOnce()
    expect(submit).toHaveBeenCalledWith(expect.any(Object), {
      strategy: 'interrupt',
      requestId: 'request-new-steer',
    })
    expect(api.promote).toHaveBeenCalledWith('conversation-1', 'input-1', 1)
  })

  it('reconciles paused reload and explicit resume from server state', async () => {
    // Given a paused durable queue.
    const pending = queuedInput()
    vi.mocked(api.list).mockResolvedValue(queueList([pending], true))
    vi.mocked(api.resume).mockResolvedValue(queueList([pending], false))
    const queue = createServerMessageQueue({
      conversationId: 'conversation-1',
      api,
      submit,
      createRequestId: () => 'unused',
      onClaimedRun,
    })

    // When reload and resume are performed.
    await queue.refresh()
    expect(queue.getSnapshot().queuePaused).toBe(true)
    await queue.resume()

    // Then the server-provided resumed state replaces the paused snapshot.
    expect(api.resume).toHaveBeenCalledWith('conversation-1')
    expect(queue.getSnapshot().queuePaused).toBe(false)
  })
})
