import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }))

vi.mock('../client', () => ({ apiFetch }))

import { conversationRunInputsApi } from '../conversation-run-inputs'

const pendingInput = {
  id: 'input/one',
  conversation_id: 'conversation/one',
  run_id: null,
  client_request_id: 'request-1',
  source: 'chat',
  status: 'pending',
  priority: 0,
  position: 1,
  revision: 3,
  input_payload: { messages: [{ role: 'user', content: 'queued' }] },
  attachment_ids: ['attachment-1'],
  checkpoint_id: null,
  claimed_at: null,
  created_at: '2026-09-06T00:00:00',
  updated_at: '2026-09-06T00:00:01',
}

describe('conversationRunInputsApi', () => {
  beforeEach(() => {
    apiFetch.mockReset()
  })

  it('parses the authoritative queue list when the response is valid', async () => {
    // Given a valid server queue envelope.
    apiFetch.mockResolvedValue({ queue_paused: false, items: [pendingInput] })

    // When the conversation queue is loaded.
    const result = await conversationRunInputsApi.list('conversation/one')

    // Then the encoded owner-scoped endpoint and parsed server item are returned.
    expect(apiFetch).toHaveBeenCalledWith('/api/conversations/conversation%2Fone/run-inputs')
    expect(result.items[0]?.attachment_ids).toEqual(['attachment-1'])
  })

  it('sends revisioned edit, delete, reorder, and promotion mutations', async () => {
    // Given successful server mutation responses.
    apiFetch
      .mockResolvedValueOnce(pendingInput)
      .mockResolvedValueOnce({ ...pendingInput, status: 'canceled' })
      .mockResolvedValueOnce([pendingInput])
      .mockResolvedValueOnce({ ...pendingInput, priority: 100 })

    // When every pending-item mutation is issued.
    await conversationRunInputsApi.edit('conversation/one', 'input/one', {
      expectedRevision: 3,
      input: { messages: [{ role: 'user', content: 'edited' }] },
    })
    await conversationRunInputsApi.remove('conversation/one', 'input/one', 4)
    await conversationRunInputsApi.reorder('conversation/one', [
      { id: 'input/two', revision: 2 },
      { id: 'input/one', revision: 4 },
    ])
    await conversationRunInputsApi.promote('conversation/one', 'input/one', 5)

    // Then each request carries the exact current revision without reusing a run request id.
    expect(apiFetch.mock.calls).toEqual([
      [
        '/api/conversations/conversation%2Fone/run-inputs/input%2Fone',
        {
          method: 'PATCH',
          body: JSON.stringify({
            expected_revision: 3,
            input: { messages: [{ role: 'user', content: 'edited' }] },
          }),
        },
      ],
      [
        '/api/conversations/conversation%2Fone/run-inputs/input%2Fone?expected_revision=4',
        { method: 'DELETE' },
      ],
      [
        '/api/conversations/conversation%2Fone/run-inputs/reorder',
        {
          method: 'POST',
          body: JSON.stringify({
            ordered_input_ids: ['input/two', 'input/one'],
            expected_revisions: { 'input/two': 2, 'input/one': 4 },
          }),
        },
      ],
      [
        '/api/conversations/conversation%2Fone/run-inputs/input%2Fone/promote',
        { method: 'POST', body: JSON.stringify({ expected_revision: 5 }) },
      ],
    ])
  })

  it('rejects malformed queue responses at the API boundary', async () => {
    // Given a response whose revision cannot be used for safe mutation.
    apiFetch.mockResolvedValue({ queue_paused: false, items: [{ ...pendingInput, revision: 0 }] })

    // When the response crosses the client boundary.
    const result = conversationRunInputsApi.list('conversation/one')

    // Then parsing fails instead of admitting an unusable queue item.
    await expect(result).rejects.toMatchObject({ name: 'ZodError' })
  })

  it('resumes the durable queue through its explicit owner-scoped endpoint', async () => {
    // Given a paused queue response that becomes active.
    apiFetch.mockResolvedValue({ queue_paused: false, items: [pendingInput] })

    // When the queue is explicitly resumed.
    const result = await conversationRunInputsApi.resume('conversation/one')

    // Then the server result is parsed and the mutation endpoint is used.
    expect(apiFetch).toHaveBeenCalledWith(
      '/api/conversations/conversation%2Fone/run-inputs/resume',
      { method: 'POST' },
    )
    expect(result.queue_paused).toBe(false)
  })
})
