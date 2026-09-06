import { ProtocolSseTransportAdapter } from '@langchain/langgraph-sdk'
import type { AgentServerAdapter } from '@langchain/react'
import { describe, expect, it, vi } from 'vitest'

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

describe('ProtocolSseTransportAdapter public reconnect contract', () => {
  it('notifies on an actual disconnect, resumes by sequence, and preserves replay identities', async () => {
    const disconnect = new Error('stream disconnected')
    const encoder = new TextEncoder()
    let disconnectFirstStream!: () => void
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
    const reconnects: { attempt: number; cause: unknown }[] = []
    const fetchImpl: typeof fetch = async (_input, init) => {
      if (typeof init?.body === 'string') requestBodies.push(init.body)
      const response = responses.shift()
      if (!response) throw new Error('unexpected stream request')
      return response
    }
    const adapter = new ProtocolSseTransportAdapter({
      apiUrl: 'http://adapter.test',
      fetchFactory: () => fetchImpl,
      idleReconnect: 0,
      maxReconnectAttempts: 1,
      onReconnect: (detail) => reconnects.push(detail),
      reconnectDelayMs: () => 0,
      threadId: 'thread-1',
    })
    const structurallyCompatible: AgentServerAdapter = adapter
    expect(structurallyCompatible).toBe(adapter)
    const handle = adapter.openEventStream({ channels: ['values'] })
    const iterator = handle.events[Symbol.asyncIterator]()

    await handle.ready
    const first = await iterator.next()
    expect(first).toMatchObject({
      done: false,
      value: { event_id: 'event-7', seq: 7, type: 'values' },
    })
    disconnectFirstStream()
    await vi.waitFor(() => {
      expect(reconnects).toEqual([{ attempt: 1, cause: disconnect }])
    })

    const second = await iterator.next()
    expect(second).toMatchObject({
      done: false,
      value: { event_id: 'event-8', seq: 8, type: 'values' },
    })
    expect(JSON.parse(requestBodies.at(1) ?? '')).toEqual({ channels: ['values'], since: 7 })
    expect(await iterator.next()).toMatchObject({ done: true })
    expect(reconnects).toHaveLength(1)

    await adapter.close()
  })

  it('aborts a pending public SSE subscription and rejects ready during cleanup', async () => {
    const aborted = new Error('subscription aborted')
    let abortCount = 0
    let startFetch: (() => void) | null = null
    const fetchStarted = new Promise<void>((resolve) => {
      startFetch = resolve
    })
    const fetchImpl: typeof fetch = async (_input, init) =>
      await new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal
        if (!signal) {
          reject(new Error('stream request is missing an abort signal'))
          return
        }
        signal.addEventListener(
          'abort',
          () => {
            abortCount += 1
            reject(aborted)
          },
          { once: true },
        )
        startFetch?.()
      })
    const adapter = new ProtocolSseTransportAdapter({
      apiUrl: 'http://adapter.test',
      fetchFactory: () => fetchImpl,
      idleReconnect: 0,
      maxReconnectAttempts: 1,
      reconnectDelayMs: () => 0,
      threadId: 'thread-1',
    })
    const handle = adapter.openEventStream({ channels: ['values'] })

    await fetchStarted
    handle.close()

    await expect(handle.ready).rejects.toBe(aborted)
    await vi.waitFor(() => {
      expect(abortCount).toBe(1)
    })
    expect(await handle.events[Symbol.asyncIterator]().next()).toMatchObject({ done: true })
    await adapter.close()
  })
})
