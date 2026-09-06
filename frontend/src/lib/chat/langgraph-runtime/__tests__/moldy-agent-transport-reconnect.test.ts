import { describe, expect, it, vi } from 'vitest'
import { createMoldyAgentTransport } from '../moldy-agent-transport'

function sseResponse(events: readonly object[]): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const event of events) {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`))
        }
        controller.close()
      },
    }),
    { status: 200, headers: { 'content-type': 'text/event-stream' } },
  )
}

describe('createMoldyAgentTransport reconnect integration', () => {
  it('publishes bounded reconnect state and clears it after replay', async () => {
    const disconnect = new Error('stream disconnected')
    const encoder = new TextEncoder()
    let disconnectFirstStream: () => void = () => {}
    const responses = [
      new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              encoder.encode('data: {"event_id":"event-7","seq":7,"type":"values"}\n\n'),
            )
            disconnectFirstStream = () => controller.error(disconnect)
          },
        }),
        { status: 200, headers: { 'content-type': 'text/event-stream' } },
      ),
      sseResponse([{ event_id: 'event-8', seq: 8, type: 'values' }]),
    ]
    const requestBodies: string[] = []
    const fetchImpl: typeof fetch = async (_input, init) => {
      if (typeof init?.body === 'string') requestBodies.push(init.body)
      const response = responses.shift()
      if (!response) throw new Error('unexpected request')
      return response
    }
    const reconnectStates: string[] = []
    const transport = createMoldyAgentTransport('conversation-1', 'agent-1', {
      apiBase: 'http://api.test',
      fetch: fetchImpl,
      onReconnectStateChange: (state) => reconnectStates.push(state),
      reconnectDelayMs: () => 0,
    })
    const handle = transport.openEventStream?.({ channels: ['values'] })
    if (!handle) throw new Error('transport does not expose an event stream')
    const iterator = handle.events[Symbol.asyncIterator]()

    await handle.ready
    await expect(iterator.next()).resolves.toMatchObject({
      value: { event_id: 'event-7', seq: 7 },
    })
    disconnectFirstStream()
    await vi.waitFor(() => expect(reconnectStates).toEqual(['reconnecting']))

    await expect(iterator.next()).resolves.toMatchObject({
      value: { event_id: 'event-8', seq: 8 },
    })
    expect(JSON.parse(requestBodies.at(1) ?? '')).toEqual({ channels: ['values'], since: 7 })
    expect(reconnectStates).toEqual(['reconnecting', 'idle'])
    await transport.close()
  })

  it('keeps readState side-effect-free during reconnect and clears on close', async () => {
    const disconnect = new Error('stream disconnected')
    let disconnectStream: () => void = () => {}
    const fetchImpl: typeof fetch = async (_input, init) => {
      if (init?.method === 'GET') {
        return new Response(
          JSON.stringify({
            values: { messages: [] },
            metadata: { latest_run: { id: 'run-1', status: 'canceled' } },
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        )
      }
      return new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            disconnectStream = () => controller.error(disconnect)
          },
        }),
        { status: 200, headers: { 'content-type': 'text/event-stream' } },
      )
    }
    const reconnectStates: string[] = []
    const hydratedStates: unknown[] = []
    const transport = createMoldyAgentTransport('conversation-1', 'agent-1', {
      apiBase: 'http://api.test',
      fetch: fetchImpl,
      onState: (state) => hydratedStates.push(state),
      onReconnectStateChange: (state) => reconnectStates.push(state),
      reconnectDelayMs: () => 0,
    })
    const deactivate = transport.activateStateHydration()
    const handle = transport.openEventStream?.({ channels: ['values'] })
    if (!handle) throw new Error('transport does not expose an event stream')

    await handle.ready
    disconnectStream()
    await vi.waitFor(() => expect(reconnectStates).toEqual(['reconnecting']))
    await transport.readState()

    expect(hydratedStates).toEqual([])
    expect(reconnectStates).toEqual(['reconnecting'])
    await transport.close()
    expect(reconnectStates).toEqual(['reconnecting', 'idle'])
    deactivate()
  })
})
