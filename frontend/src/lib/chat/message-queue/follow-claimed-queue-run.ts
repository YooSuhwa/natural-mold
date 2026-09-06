interface ClaimedQueueSubscription extends AsyncIterable<unknown> {
  unsubscribe(): Promise<void>
}

interface ClaimedQueueThread {
  subscribe(params: {
    channels: readonly ['values', 'lifecycle']
    namespaces: readonly [readonly []]
  }): Promise<ClaimedQueueSubscription>
}

type ClaimedQueueFollowOutcome = 'terminal' | 'ended' | 'stale'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function eventData(event: unknown): unknown {
  if (!isRecord(event) || !isRecord(event.params)) return undefined
  return event.params.data
}

function isClaimedRunTerminal(event: unknown, runId: string): boolean {
  if (!isRecord(event) || event.method !== 'lifecycle') return false
  if (typeof event.event_id !== 'string') return false
  if (!isRecord(event.params) || !Array.isArray(event.params.namespace)) return false
  if (event.params.namespace.length !== 0) return false
  const data = eventData(event)
  if (!isRecord(data)) return false
  return (
    event.event_id === `${runId}:lifecycle:${String(data.event)}` &&
    (data.event === 'completed' || data.event === 'failed' || data.event === 'interrupted')
  )
}

export function claimedQueueRunIdAfterTransition(
  currentRunId: string | null,
  runId: string,
  inFlight: boolean,
): string | null {
  if (inFlight) return runId
  return currentRunId === runId ? null : currentRunId
}

export async function followClaimedQueueRunValues(
  thread: ClaimedQueueThread,
  runId: string,
  onValues: (values: unknown) => void,
  isCurrent: () => boolean,
  onInFlightChange?: (inFlight: boolean) => void,
  signal?: AbortSignal,
): Promise<ClaimedQueueFollowOutcome> {
  onInFlightChange?.(true)
  let unsubscribe: (() => Promise<void>) | undefined
  const cancel = () => {
    void unsubscribe?.().catch(() => {})
  }
  try {
    const subscription = await thread.subscribe({
      channels: ['values', 'lifecycle'],
      namespaces: [[]],
    })
    unsubscribe = () => subscription.unsubscribe()
    signal?.addEventListener('abort', cancel, { once: true })
    if (signal?.aborted) return 'stale'
    for await (const event of subscription) {
      if (signal?.aborted || !isCurrent()) return 'stale'
      if (isRecord(event) && event.method === 'values') onValues(eventData(event))
      if (isClaimedRunTerminal(event, runId)) return 'terminal'
    }
    return 'ended'
  } finally {
    signal?.removeEventListener('abort', cancel)
    onInFlightChange?.(false)
    void unsubscribe?.().catch(() => {})
  }
}
