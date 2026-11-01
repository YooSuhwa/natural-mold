import { describe, expect, it, vi } from 'vitest'

import { followClaimedQueueRunValues } from '../follow-claimed-queue-run'

/** A thread whose stream stays open and never replays the run's terminal event. */
function silentThread(events: readonly unknown[] = []) {
  const unsubscribe = vi.fn(async () => {})
  const thread = {
    subscribe: vi.fn(async () => ({
      unsubscribe,
      async *[Symbol.asyncIterator]() {
        for (const event of events) yield event
        await new Promise<void>(() => {})
      },
    })),
  }
  return { thread, unsubscribe }
}

function completedLifecycle(runId: string) {
  return {
    event_id: `${runId}:lifecycle:completed`,
    method: 'lifecycle',
    params: { namespace: [], data: { event: 'completed' } },
  }
}

describe('followClaimedQueueRunValues when the claimed run already ended', () => {
  it('ends as terminal when the run settled before the subscription opened', async () => {
    const { thread, unsubscribe } = silentThread()
    const inFlight: boolean[] = []

    const outcome = await followClaimedQueueRunValues(
      thread,
      'run-fast',
      () => {},
      () => true,
      (value) => inFlight.push(value),
      undefined,
      async () => true,
    )

    expect(outcome).toBe('terminal')
    expect(inFlight).toEqual([true, false])
    expect(unsubscribe).toHaveBeenCalled()
  })

  it('keeps following the stream while the run is still active', async () => {
    const { thread } = silentThread([completedLifecycle('run-live')])
    const isRunSettled = vi.fn(async () => false)

    const outcome = await followClaimedQueueRunValues(
      thread,
      'run-live',
      () => {},
      () => true,
      undefined,
      undefined,
      isRunSettled,
    )

    expect(isRunSettled).toHaveBeenCalledTimes(1)
    expect(outcome).toBe('terminal')
  })

  it('treats a failed status check as not settled', async () => {
    const { thread } = silentThread([completedLifecycle('run-check-error')])

    const outcome = await followClaimedQueueRunValues(
      thread,
      'run-check-error',
      () => {},
      () => true,
      undefined,
      undefined,
      async () => {
        throw new Error('status unavailable')
      },
    )

    expect(outcome).toBe('terminal')
  })

  it('reports stale when the follow was replaced during the status check', async () => {
    const { thread } = silentThread()
    let current = true

    const outcome = await followClaimedQueueRunValues(
      thread,
      'run-replaced',
      () => {},
      () => current,
      undefined,
      undefined,
      async () => {
        current = false
        return true
      },
    )

    expect(outcome).toBe('stale')
  })
})
