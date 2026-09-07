import { isActiveRunStatus } from '@/lib/chat-runs/status'
import type { ConversationRun } from '@/lib/types'

const DEFAULT_RETRY_MS = 1_000
const DEFAULT_REQUEST_TIMEOUT_MS = 10_000

interface FollowExactRunOptions {
  readonly runId: string
  readonly signal: AbortSignal
  readonly readRun: (runId: string, signal: AbortSignal) => Promise<ConversationRun>
  readonly onTerminal: (run: ConversationRun) => void
  readonly onRecoverableError: (error: unknown) => void
  readonly retryMs?: number
  readonly requestTimeoutMs?: number
}

function abortableDelay(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve()
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', handleAbort)
      resolve()
    }, ms)
    const handleAbort = (): void => {
      clearTimeout(timer)
      resolve()
    }
    signal.addEventListener('abort', handleAbort, { once: true })
  })
}

async function readWithTimeout(
  readRun: FollowExactRunOptions['readRun'],
  runId: string,
  parentSignal: AbortSignal,
  timeoutMs: number,
): Promise<ConversationRun> {
  const requestController = new AbortController()
  const abortRequest = (): void => requestController.abort(parentSignal.reason)
  parentSignal.addEventListener('abort', abortRequest, { once: true })
  const timeout = setTimeout(() => requestController.abort(), timeoutMs)
  try {
    return await readRun(runId, requestController.signal)
  } finally {
    clearTimeout(timeout)
    parentSignal.removeEventListener('abort', abortRequest)
  }
}

export async function followExactRunToTerminal({
  runId,
  signal,
  readRun,
  onTerminal,
  onRecoverableError,
  retryMs = DEFAULT_RETRY_MS,
  requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
}: FollowExactRunOptions): Promise<void> {
  while (!signal.aborted) {
    try {
      const run = await readWithTimeout(readRun, runId, signal, requestTimeoutMs)
      if (signal.aborted) return
      if (run.id === runId && !isActiveRunStatus(run.status)) {
        onTerminal(run)
        return
      }
    } catch (error) {
      if (signal.aborted) return
      onRecoverableError(error)
    }
    await abortableDelay(retryMs, signal)
  }
}
