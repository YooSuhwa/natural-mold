import { afterEach, describe, expect, it, vi } from 'vitest'
import { csrfStore } from '@/lib/auth/csrf'
import { createMoldyAgentTransport } from '../moldy-agent-transport'

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
    ...init,
  })
}

describe('createMoldyAgentTransport', () => {
  afterEach(() => {
    csrfStore.clear()
  })

  it('routes protocol commands to the conversation-scoped BFF path with auth headers', async () => {
    csrfStore.set('csrf-1')
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        type: 'success',
        id: 1,
        result: { run_id: 'run-1' },
      }),
    )
    const transport = createMoldyAgentTransport('conversation 1', 'agent-1', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
    })

    await transport.send({
      id: 1,
      method: 'run.start',
      params: { assistant_id: '_', input: { messages: [] } },
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe(
      'http://api.test/api/conversations/conversation%201/langgraph/threads/conversation%201/commands',
    )
    expect(init?.method).toBe('POST')
    expect(init?.credentials).toBe('include')
    expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('csrf-1')
    expect(JSON.parse(String(init?.body))).toMatchObject({
      method: 'run.start',
      params: { assistant_id: 'agent-1' },
    })
  })

  it('uses the state path for SDK hydration without adding CSRF to GET requests', async () => {
    csrfStore.set('csrf-2')
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        values: { messages: [] },
        next: [],
        tasks: [],
      }),
    )
    const transport = createMoldyAgentTransport('conversation-2', 'agent-2', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
    })

    await transport.getState?.()

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe(
      'http://api.test/api/conversations/conversation-2/langgraph/threads/conversation-2/state',
    )
    expect(init?.method).toBe('GET')
    expect(init?.credentials).toBe('include')
    expect(new Headers(init?.headers).has('X-CSRF-Token')).toBe(false)
  })
})
