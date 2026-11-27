import type { QueueRunStartAcceptance } from '@/lib/chat/message-queue/server-message-queue-contract'

export interface MoldyAgUiAcceptance {
  readonly publicRunId: string
  readonly physicalRunId: string | null
  readonly queue: QueueRunStartAcceptance | null
}

function integerHeader(headers: Headers, name: string): number | null {
  const raw = headers.get(name)
  if (raw === null || !/^\d+$/.test(raw)) return null
  const number = Number(raw)
  return Number.isSafeInteger(number) ? number : null
}

export function readAcceptance(headers: Headers, requestedId: string): MoldyAgUiAcceptance {
  const publicRunId = headers.get('X-Run-Id')
  if (publicRunId && publicRunId !== requestedId) throw new Error('Unexpected chat run receipt.')
  const physicalRunId = headers.get('X-Moldy-Run-Id')
  const inputId = headers.get('X-Moldy-Input-Id')
  const inputStatus = headers.get('X-Moldy-Input-Status')
  const position = integerHeader(headers, 'X-Moldy-Input-Position')
  const revision = integerHeader(headers, 'X-Moldy-Input-Revision')
  const queue: QueueRunStartAcceptance | null =
    inputId &&
    (inputStatus === 'pending' || inputStatus === 'claimed') &&
    position !== null &&
    revision !== null
      ? {
          inputId,
          inputStatus,
          revision,
          position,
          ...(physicalRunId ? { runId: physicalRunId } : {}),
        }
      : null
  return { publicRunId: requestedId, physicalRunId, queue }
}
