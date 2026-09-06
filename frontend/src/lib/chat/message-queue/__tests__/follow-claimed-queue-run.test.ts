import { Client } from '@langchain/langgraph-sdk'
import { StreamController, type AgentServerAdapter } from '@langchain/langgraph-sdk/stream'
import { act, renderHook } from '@testing-library/react'
import { useStream } from '@langchain/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'

import { followClaimedQueueRunValues } from '../follow-claimed-queue-run'
import { server } from '../../../../../tests/setup'

describe('followClaimedQueueRunValues', () => {
  it('replays an externally accepted run through the real SDK thread without another run.start', async () => {
    const replay = [
      {
        type: 'event',
        event_id: 'run-claimed:protocol:00000001',
        seq: 1,
        method: 'values',
        params: {
          namespace: [],
          timestamp: Date.now(),
          data: {
            messages: [
              { type: 'human', id: 'human-1', content: 'queued prompt' },
              { type: 'ai', id: 'ai-1', content: 'visible without reload' },
            ],
          },
        },
      },
      {
        type: 'event',
        event_id: 'run-claimed:lifecycle:completed',
        seq: 2,
        method: 'lifecycle',
        params: {
          namespace: [],
          timestamp: Date.now(),
          data: { event: 'completed' },
        },
      },
    ]
    type EventStreamHandle = ReturnType<NonNullable<AgentServerAdapter['openEventStream']>>
    const events = (async function* () {
      yield* replay
    })() as EventStreamHandle['events']
    const send = vi.fn<AgentServerAdapter['send']>(async () => undefined)
    const adapter: AgentServerAdapter = {
      threadId: 'conversation-claimed',
      open: async () => {},
      close: async () => {},
      send,
      events: async function* () {},
      getState: async <StateType = unknown>() => ({
        values: { messages: [] } as unknown as StateType,
        next: [],
      }),
      getHistory: async () => [],
      openEventStream: () => ({
        ready: Promise.resolve(),
        close: () => {},
        events,
      }),
    }
    const client = new Client({ apiUrl: 'http://unused.test' })
    vi.spyOn(client.threads, 'getHistory').mockResolvedValue([])
    const controller = new StreamController({
      assistantId: '_',
      client,
      threadId: 'conversation-claimed',
      transport: adapter,
    })
    await controller.hydrate()
    const thread = controller.getThread()
    expect(thread).toBeDefined()
    if (!thread) throw new Error('StreamController did not expose its hydrated thread')
    const received: unknown[] = []

    await followClaimedQueueRunValues(
      thread,
      'run-claimed',
      (values) => received.push(values),
      () => true,
    )

    expect(received).toHaveLength(1)
    const values = received[0] as {
      messages: { id?: string; content?: unknown }[]
    }
    expect(values.messages).toEqual([
      expect.objectContaining({ id: 'human-1', content: 'queued prompt' }),
      expect.objectContaining({ id: 'ai-1', content: 'visible without reload' }),
    ])
    expect(send.mock.calls.some(([command]) => command.method === 'run.start')).toBe(false)
    await controller.dispose()
  })

  it('exposes terminal-only late replay while the public state already has final messages', async () => {
    // Given production useStream hydrated before a fast claimed run completed.
    const finalValues = {
      messages: [
        { type: 'human', id: 'human-late-claim', content: 'explicit steer message' },
        { type: 'ai', id: 'ai-late-claim', content: 'visible successor response' },
      ],
    }
    let stateReadCount = 0
    const getState = vi.fn(async () => {
      stateReadCount += 1
      return {
        values: stateReadCount === 1 ? { messages: [] } : finalValues,
        next: [],
      }
    })
    const transport = {
      threadId: 'conversation-late-claim',
      open: async () => {},
      close: async () => {},
      send: vi.fn(async (command: { method: string }) => {
        void command
      }),
      events: async function* () {},
      getState,
      getHistory: async () => [],
      openEventStream: () => ({
        ready: Promise.resolve(),
        close: () => {},
        events: (async function* () {
          yield {
            type: 'event',
            event_id: 'run-late-claim:lifecycle:completed',
            seq: 1,
            method: 'lifecycle',
            params: {
              namespace: [],
              timestamp: Date.now(),
              data: { event: 'completed' },
            },
          }
        })(),
      }),
    }
    server.use(
      http.post('http://localhost:8123/threads/conversation-late-claim/history', () =>
        HttpResponse.json([]),
      ),
    )
    const { result, unmount } = renderHook(() =>
      useStream<{ messages: unknown[] }>({
        threadId: 'conversation-late-claim',
        transport: transport as never,
      }),
    )
    const streamBeforeHydration = result.current
    await act(async () => {
      await result.current.hydrationPromise
    })
    expect(result.current).not.toBe(streamBeforeHydration)
    expect(result.current.getThread).not.toBe(streamBeforeHydration.getThread)
    expect(result.current.messages).toEqual([])
    const thread = result.current.getThread()
    expect(thread).toBeDefined()
    if (!thread) throw new Error('useStream did not expose its hydrated thread')
    const received: unknown[] = []

    // When the late follow can replay only the terminal event.
    const outcome = await followClaimedQueueRunValues(
      thread,
      'run-late-claim',
      (values) => received.push(values),
      () => true,
    )
    const authoritativeState = await transport.getState()
    expect(authoritativeState.values).toEqual(finalValues)

    // Then the SDK reports an exact terminal but does not invent a values replay.
    expect(outcome).toBe('terminal')
    expect(received).toEqual([])
    unmount()
  })
})
