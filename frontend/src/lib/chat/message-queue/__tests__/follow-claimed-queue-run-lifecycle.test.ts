import { act, renderHook, waitFor } from '@testing-library/react'
import { useStream } from '@langchain/react'
import { http, HttpResponse } from 'msw'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import {
  claimedQueueRunIdAfterTransition,
  followClaimedQueueRunValues,
} from '../follow-claimed-queue-run'
import { server } from '../../../../../tests/setup'

describe('followClaimedQueueRunValues', () => {
  it('projects the production useStream busy lifecycle while following a claimed run', async () => {
    let releaseTerminal!: () => void
    const terminalGate = new Promise<void>((resolve) => {
      releaseTerminal = resolve
    })
    const keepTransportOpen = new Promise<void>(() => {})
    const openEventStream = vi.fn(() => {
      const events = (async function* () {
        yield {
          type: 'event',
          event_id: 'run-production:lifecycle:running',
          seq: 1,
          method: 'lifecycle',
          params: {
            namespace: [],
            timestamp: Date.now(),
            data: { event: 'running' },
          },
        }
        yield {
          type: 'event',
          event_id: 'run-production:protocol:00000002',
          seq: 2,
          method: 'values',
          params: {
            namespace: [],
            timestamp: Date.now(),
            data: {
              messages: [
                { type: 'human', id: 'human-claimed', content: 'claimed prompt' },
                { type: 'ai', id: 'ai-claimed', content: 'claimed partial response' },
              ],
            },
          },
        }
        await terminalGate
        yield {
          type: 'event',
          event_id: 'run-production:lifecycle:completed',
          seq: 3,
          method: 'lifecycle',
          params: {
            namespace: [],
            timestamp: Date.now(),
            data: { event: 'completed' },
          },
        }
        await keepTransportOpen
      })()
      return {
        ready: Promise.resolve(),
        close: () => {},
        events,
      }
    })
    const transport = {
      threadId: 'conversation-production-use-stream',
      open: async () => {},
      close: async () => {},
      send: vi.fn(async (command: { method: string }) => {
        void command
      }),
      events: async function* () {},
      getState: async () => ({ values: { messages: [] }, next: [] }),
      getHistory: async () => [],
      openEventStream,
    }
    server.use(
      http.post('http://localhost:8123/threads/conversation-production-use-stream/history', () =>
        HttpResponse.json([]),
      ),
    )
    const { result, unmount } = renderHook(() => {
      const stream = useStream<{ messages: unknown[] }>({
        threadId: 'conversation-production-use-stream',
        transport: transport as never,
      })
      const [claimedRunId, setClaimedRunId] = useState<string | null>(null)
      return {
        stream,
        activeClaimedRunId: claimedRunId,
        runtimeIsRunning: stream.isLoading || claimedRunId !== null,
        stopVisible: stream.isLoading || claimedRunId !== null,
        transitionClaimedRun: (runId: string, inFlight: boolean) =>
          setClaimedRunId((current) => claimedQueueRunIdAfterTransition(current, runId, inFlight)),
      }
    })
    await act(async () => {
      await result.current.stream.hydrationPromise
    })
    const thread = result.current.stream.getThread()
    expect(thread).toBeDefined()
    if (!thread) throw new Error('useStream did not expose its hydrated thread')
    const received: unknown[] = []
    let followPromise!: ReturnType<typeof followClaimedQueueRunValues>

    act(() => {
      followPromise = followClaimedQueueRunValues(
        thread,
        'run-production',
        (values) => received.push(values),
        () => true,
        (inFlight) => result.current.transitionClaimedRun('run-production', inFlight),
      )
    })
    await waitFor(() => expect(received).toHaveLength(1))
    expect(result.current.stream.isLoading).toBe(false)
    expect(result.current.runtimeIsRunning).toBe(true)
    expect(result.current.stopVisible).toBe(true)
    const values = received[0] as { messages?: { id?: string }[] }
    expect(values.messages).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: 'ai-claimed' })]),
    )
    expect(transport.send.mock.calls.some(([command]) => command.method === 'run.start')).toBe(
      false,
    )

    act(() => result.current.transitionClaimedRun('run-next', true))
    expect(result.current.activeClaimedRunId).toBe('run-next')
    releaseTerminal()
    await act(async () => {
      await followPromise
    })
    expect(result.current.activeClaimedRunId).toBe('run-next')
    expect(result.current.runtimeIsRunning).toBe(true)
    expect(result.current.stopVisible).toBe(true)
    act(() => result.current.transitionClaimedRun('run-next', false))
    await waitFor(() => expect(result.current.runtimeIsRunning).toBe(false))
    expect(result.current.stopVisible).toBe(false)
    expect(openEventStream).toHaveBeenCalled()
    unmount()
  })

  it.each(['pending', 'rejected'] as const)(
    'clears claimed busy state when unsubscribe is %s',
    async (unsubscribeOutcome) => {
      const unsubscribe = vi.fn(() =>
        unsubscribeOutcome === 'pending'
          ? new Promise<void>(() => {})
          : Promise.reject(new Error('unsubscribe failed')),
      )
      const thread = {
        subscribe: async () => ({
          unsubscribe,
          async *[Symbol.asyncIterator]() {
            yield {
              type: 'event',
              event_id: 'run-cleanup:lifecycle:completed',
              method: 'lifecycle',
              params: { namespace: [], data: { event: 'completed' } },
            }
            await new Promise<void>(() => {})
          },
        }),
      }
      const busy: boolean[] = []

      await followClaimedQueueRunValues(
        thread,
        'run-cleanup',
        () => {},
        () => true,
        (inFlight) => busy.push(inFlight),
      )

      expect(busy).toEqual([true, false])
      expect(unsubscribe).toHaveBeenCalled()
    },
  )

  it('ignores late values from an aborted predecessor follow', async () => {
    let releaseBufferedValue!: () => void
    const bufferedValue = new Promise<void>((resolve) => {
      releaseBufferedValue = resolve
    })
    const abort = new AbortController()
    const received: unknown[] = []
    const thread = {
      subscribe: async () => ({
        unsubscribe: vi.fn(async () => {}),
        async *[Symbol.asyncIterator]() {
          await bufferedValue
          yield {
            type: 'event',
            event_id: 'run-old:protocol:00000002',
            method: 'values',
            params: { namespace: [], data: { messages: [{ id: 'old-run-message' }] } },
          }
        },
      }),
    }

    const follow = followClaimedQueueRunValues(
      thread,
      'run-old',
      (values) => received.push(values),
      () => true,
      undefined,
      abort.signal,
    )
    abort.abort()
    releaseBufferedValue()
    await follow

    expect(received).toEqual([])
  })
})
