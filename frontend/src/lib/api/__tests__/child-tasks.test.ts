import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  cancelChildTask,
  getChildTask,
  listChildTasks,
  sendChildInstruction,
  waitForChildTask,
} from '../child-tasks'

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('../client', () => ({ apiFetch }))

const task = {
  id: '8610a09d-342d-4024-9003-eb42b8444c65',
  run_id: 'ad701d3a-9645-473e-a449-f7a070b5b2d2',
  tool_call_id: 'call-6543',
  role: 'researcher',
  status: 'running',
  checkpointed: true,
  created_at: '2026-11-27T01:02:03Z',
  updated_at: '2026-11-27T01:03:03Z',
  result: null,
  instructions: [],
}

describe('delegated child task requests', () => {
  beforeEach(() => apiFetch.mockReset())

  it('lists tasks in their conversation and forwards the caller’s cancellation signal', async () => {
    const signal = new AbortController().signal
    const persisted = { ...task, brief: 'Compare the municipal housing agencies.' }
    const page = {
      items: [persisted],
      next_offset: 100,
      enabled: true,
      allow_legacy_details: false,
    }
    apiFetch.mockResolvedValue(page)

    await expect(listChildTasks('conversation/one', signal, 50)).resolves.toEqual(page)

    expect(apiFetch).toHaveBeenCalledWith(
      '/api/conversations/conversation%2Fone/child-tasks?limit=50&offset=50',
      expect.objectContaining({ signal }),
    )
  })

  it('rejects an unknown lifecycle state before displaying controls for that task', async () => {
    apiFetch.mockResolvedValue({
      items: [{ ...task, status: 'unknown' }],
      next_offset: null,
      enabled: true,
      allow_legacy_details: false,
    })

    await expect(listChildTasks('conversation')).rejects.toMatchObject({ name: 'ZodError' })
  })

  it('retains filtered page continuation and rejects a non-advancing offset', async () => {
    const filtered = { items: [], next_offset: 150, enabled: false, allow_legacy_details: false }
    apiFetch.mockResolvedValue(filtered)
    await expect(listChildTasks('conversation', undefined, 100)).resolves.toEqual(filtered)
    await expect(listChildTasks('conversation', undefined, 150)).rejects.toThrow('did not advance')
  })

  it('fetches the reauthorized exact child without waiting', async () => {
    const signal = new AbortController().signal
    apiFetch.mockResolvedValue(task)
    await expect(getChildTask('conversation/one', 'child/two', signal)).resolves.toEqual(task)
    expect(apiFetch).toHaveBeenCalledWith(
      '/api/conversations/conversation%2Fone/child-tasks/child%2Ftwo/wait?timeout_seconds=0',
      { signal },
    )
  })

  it('requires explicit legacy authorization even on an empty disabled page', async () => {
    apiFetch.mockResolvedValue({ items: [], enabled: false, next_offset: null })
    await expect(listChildTasks('conversation')).rejects.toMatchObject({ name: 'ZodError' })
  })

  it('addresses send and stop to exactly the chosen child and preserves the request id', async () => {
    const input = {
      client_request_id: '6184f6fd-fcf2-4599-8117-a07432771c83',
      text: 'Compare public agencies only.',
    }
    apiFetch.mockResolvedValue(task)

    await sendChildInstruction('conversation/one', 'child/two', input)
    await cancelChildTask('conversation/one', 'child/two')

    expect(apiFetch.mock.calls[0]).toEqual([
      '/api/conversations/conversation%2Fone/child-tasks/child%2Ftwo/instructions',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(input) }),
    ])
    expect(apiFetch.mock.calls[1]).toEqual([
      '/api/conversations/conversation%2Fone/child-tasks/child%2Ftwo/cancel',
      expect.objectContaining({ method: 'POST' }),
    ])
  })

  it('waits with a time limit and leaves the child running on timeout', async () => {
    const signal = new AbortController().signal
    apiFetch.mockResolvedValue(task)

    await expect(waitForChildTask('conversation/one', 'child/two', signal)).resolves.toEqual(task)

    expect(apiFetch).toHaveBeenCalledTimes(1)
    const [path, options] = apiFetch.mock.calls[0]
    expect(path).toBe(
      '/api/conversations/conversation%2Fone/child-tasks/child%2Ftwo/wait?timeout_seconds=10',
    )
    expect(options.signal).toBe(signal)
    expect(options.method ?? 'GET').toBe('GET')
  })
})
