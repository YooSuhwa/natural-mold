import { expect, it, vi } from 'vitest'
import { conversationRunSteersApi } from '../conversation-run-steers'
import { apiFetch } from '../client'

vi.mock('../client', () => ({ apiFetch: vi.fn() }))

it('loads an older window by sequence and submits text to the exact encoded run', async () => {
  const row = {
    id: 'steer',
    conversation_id: 'conversation/one',
    client_request_id: 'request',
    run_id: 'run/one',
    sequence: 12,
    text: 'Domestic cases only',
    status: 'applied',
    applied_at: '2026-11-27T10:01:02Z',
    created_at: '2026-11-27T10:01:01Z',
    queued_input_id: null,
  }
  vi.mocked(apiFetch).mockResolvedValueOnce([row]).mockResolvedValueOnce(row)
  const signal = new AbortController().signal

  const earlier = await conversationRunSteersApi.listBefore('conversation/one', 13, signal)
  expect(earlier).toEqual([row])
  expect(apiFetch).toHaveBeenNthCalledWith(
    1,
    '/api/conversations/conversation%2Fone/steers?before_sequence=13&limit=50',
    { signal },
  )
  await conversationRunSteersApi.submit('conversation/one', {
    runId: 'run/one',
    requestId: 'request',
    text: row.text,
  })
  expect(apiFetch).toHaveBeenNthCalledWith(
    2,
    '/api/conversations/conversation%2Fone/runs/run%2Fone/steers',
    { method: 'POST', body: JSON.stringify({ client_request_id: 'request', text: row.text }) },
  )
})
