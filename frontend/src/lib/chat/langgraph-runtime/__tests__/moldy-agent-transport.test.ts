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

  it('leaves ordinary run.start acceptance to the official stream callback', async () => {
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

    expect(onRunStartAccepted).not.toHaveBeenCalled()
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

  it('persists queue input directly with strategy, request id, attachments, and context', async () => {
    const onRunStartAccepted = vi.fn()
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        type: 'success',
        id: 'queue-request-1',
        result: {
          input_id: 'input-1',
          input_status: 'pending',
          revision: 1,
          position: 2,
        },
      }),
    )
    const transport = createMoldyAgentTransport('conversation-queue', 'agent-queue', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
      onRunStartAccepted,
    })

    const accepted = await transport.submitQueuedInput(
      {
        role: 'user',
        content: [{ type: 'text', text: 'queued content' }],
        attachments: [
          {
            id: 'attachment-1',
            type: 'document',
            name: 'queue.txt',
            contentType: 'text/plain',
            content: [],
            status: { type: 'complete' },
          },
        ],
        createdAt: new Date('2026-09-06T00:00:00Z'),
        parentId: null,
        sourceId: null,
        runConfig: {
          custom: {
            resource_context: [
              {
                kind: 'artifact',
                id: '11111111-1111-4111-8111-111111111111',
                version_id: '22222222-2222-4222-8222-222222222222',
                label: 'Pinned artifact',
              },
            ],
          },
        },
        metadata: { custom: {} },
      },
      'enqueue',
      'queue-request-1',
    )

    expect(accepted).toEqual({
      inputId: 'input-1',
      inputStatus: 'pending',
      revision: 1,
      position: 2,
    })
    expect(fetchMock).toHaveBeenCalledOnce()
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body))
    expect(body).toMatchObject({
      id: expect.any(Number),
      method: 'run.start',
      params: {
        assistant_id: 'agent-queue',
        multitask_strategy: 'enqueue',
        client_request_id: 'queue-request-1',
        input: {
          attachments: [{ id: 'attachment-1' }],
          resource_context: [
            {
              kind: 'artifact',
              id: '11111111-1111-4111-8111-111111111111',
              version_id: '22222222-2222-4222-8222-222222222222',
              label: 'Pinned artifact',
            },
          ],
          messages: [
            {
              role: 'user',
              content: [{ type: 'text', text: 'queued content' }],
              metadata: {},
            },
          ],
        },
      },
    })
    expect(onRunStartAccepted).not.toHaveBeenCalled()
  })

  it('keeps the accepted-run callback for a directly claimed interrupt input', async () => {
    const onRunStartAccepted = vi.fn()
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        type: 'success',
        id: 'interrupt-request-1',
        result: {
          input_id: 'input-interrupt',
          input_status: 'claimed',
          revision: 2,
          position: 1,
          run_id: 'run-interrupt',
        },
      }),
    )
    const transport = createMoldyAgentTransport('conversation-interrupt', 'agent-interrupt', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
      onRunStartAccepted,
    })

    const accepted = await transport.submitQueuedInput(
      {
        role: 'user',
        content: [{ type: 'text', text: 'steer now' }],
        attachments: [],
        createdAt: new Date('2026-09-06T00:00:00Z'),
        parentId: null,
        sourceId: null,
        runConfig: {},
        metadata: { custom: {} },
      },
      'interrupt',
      'interrupt-request-1',
    )

    expect(accepted).toMatchObject({ inputStatus: 'claimed', runId: 'run-interrupt' })
    expect(onRunStartAccepted).toHaveBeenCalledExactlyOnceWith('run-interrupt')
  })

  it('rejects an array-valued queue status instead of coercing it to a pending literal', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        type: 'success',
        id: 'malformed-status',
        result: {
          input_id: 'input-malformed',
          input_status: ['pending'],
          revision: 1,
          position: 1,
        },
      }),
    )
    const transport = createMoldyAgentTransport('conversation-malformed', 'agent-malformed', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
    })

    await expect(
      transport.submitQueuedInput(
        {
          role: 'user',
          content: [{ type: 'text', text: 'reject malformed acceptance' }],
          attachments: [],
          createdAt: new Date('2026-09-06T00:00:00Z'),
          parentId: null,
          sourceId: null,
          runConfig: {},
          metadata: { custom: {} },
        },
        'enqueue',
        'malformed-status',
      ),
    ).rejects.toThrow('Queue submission response is malformed')
  })

  it('retries the durable failed input instead of the prior successful assistant turn', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        type: 'success',
        id: 'retry-attempt-new',
        result: {
          input_id: 'input-retry-new',
          input_status: 'claimed',
          revision: 1,
          position: 1,
          run_id: 'run-retry-new',
        },
      }),
    )
    const transport = createMoldyAgentTransport('conversation-retry', 'agent-retry', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
    })
    const durableFailedInput = {
      id: 'input-failed-original',
      conversation_id: 'conversation-retry',
      run_id: 'run-failed-original',
      client_request_id: 'request-original',
      source: 'user',
      status: 'claimed' as const,
      priority: 0,
      position: 1,
      revision: 1,
      input_payload: {
        messages: [
          { role: 'user', content: [{ type: 'text', text: 'successful earlier question' }] },
          { role: 'assistant', content: [{ type: 'text', text: 'prior successful answer' }] },
          { role: 'user', content: [{ type: 'text', text: 'the input that actually failed' }] },
        ],
      },
      resource_context: [
        {
          kind: 'file' as const,
          id: '11111111-1111-4111-8111-111111111111',
          label: 'Failure context',
        },
      ],
      attachment_ids: [],
      checkpoint_id: null,
      claimed_at: '2026-09-06T00:00:00Z',
      created_at: '2026-09-06T00:00:00Z',
      updated_at: '2026-09-06T00:00:00Z',
    }

    await transport.retryFailedInput(durableFailedInput, 'retry-attempt-new')

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body))
    expect(body.params.client_request_id).toBe('retry-attempt-new')
    expect(body.params.client_request_id).not.toBe(durableFailedInput.client_request_id)
    expect(body.params.input.messages).toEqual(durableFailedInput.input_payload.messages)
    expect(body.params.input.messages.at(-1)).toEqual({
      role: 'user',
      content: [{ type: 'text', text: 'the input that actually failed' }],
    })
    expect(body.params.input.resource_context).toEqual(durableFailedInput.resource_context)
  })

  it('never retries a still-pending accepted input', async () => {
    const fetchMock = vi.fn<typeof fetch>()
    const transport = createMoldyAgentTransport('conversation-pending', 'agent-pending', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
    })

    await expect(
      transport.retryFailedInput(
        {
          id: 'input-pending',
          conversation_id: 'conversation-pending',
          run_id: null,
          client_request_id: 'request-pending',
          source: 'user',
          status: 'pending',
          priority: 0,
          position: 1,
          revision: 1,
          input_payload: { messages: [] },
          resource_context: [],
          attachment_ids: [],
          checkpoint_id: null,
          claimed_at: null,
          created_at: '2026-09-06T00:00:00Z',
          updated_at: '2026-09-06T00:00:00Z',
        },
        'retry-pending-new',
      ),
    ).rejects.toThrow('Only an accepted failed run input can be retried')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('rejects a server-only resource snapshot before retrying an input without public refs', async () => {
    const fetchMock = vi.fn<typeof fetch>()
    const transport = createMoldyAgentTransport('conversation-dirty-retry', 'agent-dirty', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
    })

    await expect(
      transport.retryFailedInput(
        {
          id: 'input-dirty',
          conversation_id: 'conversation-dirty-retry',
          run_id: 'run-dirty',
          client_request_id: 'request-dirty',
          source: 'user',
          status: 'failed',
          priority: 0,
          position: 1,
          revision: 1,
          input_payload: {
            messages: [],
            _moldy_resource_context_v1: { snapshots: ['server-only'] },
          },
          resource_context: [],
          attachment_ids: [],
          checkpoint_id: null,
          claimed_at: '2026-09-06T00:00:00Z',
          created_at: '2026-09-06T00:00:00Z',
          updated_at: '2026-09-06T00:00:00Z',
        },
        'retry-dirty-new',
      ),
    ).rejects.toThrow('Resource context cannot be retried: reserved-server-field')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('rejects dirty resource references before issuing run.start', async () => {
    const fetchMock = vi.fn<typeof fetch>()
    const transport = createMoldyAgentTransport('conversation-dirty', 'agent-dirty', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
    })

    const submission = transport.submitQueuedInput(
      {
        role: 'user',
        content: [{ type: 'text', text: 'do not send this' }],
        attachments: [],
        createdAt: new Date('2026-09-06T00:00:00Z'),
        parentId: null,
        sourceId: null,
        runConfig: {
          custom: {
            resource_context: [
              {
                kind: 'file',
                id: '11111111-1111-4111-8111-111111111111',
                path: '/tmp/private',
              },
            ],
          },
        },
        metadata: { custom: {} },
      },
      'enqueue',
      'queue-dirty',
    )

    await expect(submission).rejects.toThrow('Resource context cannot be submitted: malformed')
    expect(fetchMock).not.toHaveBeenCalled()
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

  it('reads terminal state without notifying active hydration listeners', async () => {
    const onState = vi.fn()
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        values: { messages: [{ id: 'message-terminal' }] },
        next: [],
        tasks: [],
      }),
    )
    const transport = createMoldyAgentTransport('conversation-terminal', 'agent-terminal', {
      apiBase: 'http://api.test',
      fetch: fetchMock,
      onState,
    })
    const deactivate = transport.activateStateHydration()

    await expect(transport.readState()).resolves.toEqual({
      values: { messages: [{ id: 'message-terminal' }] },
      next: [],
      tasks: [],
    })

    expect(onState).not.toHaveBeenCalled()
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
