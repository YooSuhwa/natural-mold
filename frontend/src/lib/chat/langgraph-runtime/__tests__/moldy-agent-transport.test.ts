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
    expect(Reflect.get(globalThis, Symbol.for('langgraph_api:url'))).toBe('http://api.test')
    expect(Reflect.get(globalThis, Symbol.for('langgraph_api:fetch'))).toEqual(expect.any(Function))
    expect('apiUrl' in transport).toBe(false)
  })

  it('notifies with the accepted run id after a run.start command succeeds', async () => {
    const onRunStartAccepted = vi.fn()
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        type: 'success',
        id: 1,
        result: { run_id: 'run-1' },
      }),
    )
    const transport = createMoldyAgentTransport('conversation-1', 'agent-1', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
      onRunStartAccepted,
    })

    await transport.send({
      id: 1,
      method: 'run.start',
      params: { assistant_id: '_', input: { messages: [] } },
    })

    expect(onRunStartAccepted).toHaveBeenCalledExactlyOnceWith('run-1')
  })

  it('does not notify when a protocol error response includes a run-like value', async () => {
    const onRunStartAccepted = vi.fn()
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        type: 'error',
        id: 1,
        result: { run_id: 'not-an-accepted-run' },
      }),
    )
    const transport = createMoldyAgentTransport('conversation-error', 'agent-error', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
      onRunStartAccepted,
    })

    await transport
      .send({
        id: 1,
        method: 'run.start',
        params: { assistant_id: '_', input: { messages: [] } },
      })
      .catch(() => undefined)

    expect(onRunStartAccepted).not.toHaveBeenCalled()
  })

  it('does not notify when a successful protocol response omits run_id', async () => {
    const onRunStartAccepted = vi.fn()
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        type: 'success',
        id: 1,
        result: {},
      }),
    )
    const transport = createMoldyAgentTransport('conversation-no-run', 'agent-no-run', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
      onRunStartAccepted,
    })

    await transport.send({
      id: 1,
      method: 'run.start',
      params: { assistant_id: '_', input: { messages: [] } },
    })

    expect(onRunStartAccepted).not.toHaveBeenCalled()
  })

  it('uses the state path for SDK hydration without adding CSRF to GET requests', async () => {
    csrfStore.set('csrf-2')
    const onState = vi.fn()
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
      onState,
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
    expect(onState).not.toHaveBeenCalled()
    const deactivate = transport.activateStateHydration()
    expect(onState).toHaveBeenCalledWith({
      values: { messages: [] },
      next: [],
      tasks: [],
    })
    deactivate()
  })

  it('keeps the listener under StrictMode double-activate when the first deactivate runs', async () => {
    const onState = vi.fn()
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        values: { messages: [{ id: 'message-strict' }] },
        next: [],
        tasks: [],
      }),
    )
    const transport = createMoldyAgentTransport('conversation-strict', 'agent-strict', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
      onState,
    })

    // StrictMode mounts effects twice; the first cleanup must not remove the
    // second activation's listener (per-activation wrapper).
    const deactivateFirst = transport.activateStateHydration()
    const deactivateSecond = transport.activateStateHydration()
    deactivateFirst()

    await transport.getState?.()

    expect(onState).toHaveBeenCalledTimes(1)
    expect(onState).toHaveBeenCalledWith({
      values: { messages: [{ id: 'message-strict' }] },
      next: [],
      tasks: [],
    })
    deactivateSecond()
  })

  it('notifies an active state hydration listener when SDK hydration finishes later', async () => {
    const onState = vi.fn()
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        values: { messages: [{ id: 'message-1' }] },
        next: [],
        tasks: [],
      }),
    )
    const transport = createMoldyAgentTransport('conversation-3', 'agent-3', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
      onState,
    })
    const deactivate = transport.activateStateHydration()

    await transport.getState?.()

    expect(onState).toHaveBeenCalledWith({
      values: { messages: [{ id: 'message-1' }] },
      next: [],
      tasks: [],
    })
    deactivate()
  })

  it('supports installing and clearing the state hydration listener after activation', async () => {
    const onState = vi.fn()
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        values: { messages: [{ id: 'message-late-listener' }] },
        next: [],
        tasks: [],
      }),
    )
    const transport = createMoldyAgentTransport('conversation-late', 'agent-late', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
    })
    const deactivate = transport.activateStateHydration()

    transport.setStateHydrationListener(onState)
    await transport.getState?.()

    expect(onState).toHaveBeenCalledOnce()
    expect(onState).toHaveBeenCalledWith({
      values: { messages: [{ id: 'message-late-listener' }] },
      next: [],
      tasks: [],
    })

    transport.setStateHydrationListener(undefined)
    await transport.getState?.()

    expect(onState).toHaveBeenCalledOnce()
    deactivate()
  })
})
