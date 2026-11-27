import type { Page, Request, Response, TestInfo } from '@playwright/test'

const LIMIT = 10_000
const MAX_BODY_BYTES = 4 * 1024 * 1024
const READ_DEADLINE_MS = 600

type Terminal = 'none' | 'success' | 'interrupt' | 'cancelled' | 'error' | 'unknown'
type Durable = 'none' | 'active' | 'interrupted' | 'idle' | 'unavailable'

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function id(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function slots(value: unknown) {
  const list = Array.isArray(value) ? value : []
  const ids = list.map((slot) => id(record(slot).id))
  return {
    count: Math.min(list.length, LIMIT),
    approvals: Math.min(
      list.filter((slot) => record(slot).reason === 'moldy.tool_approval').length,
      LIMIT,
    ),
    readOnly: Math.min(
      list.filter((slot) => record(record(record(slot).metadata).moldy).actionable === false)
        .length,
      LIMIT,
    ),
    ids:
      Array.isArray(value) &&
      list.length <= LIMIT &&
      ids.every((slot) => slot !== null) &&
      new Set(ids).size === ids.length
        ? ids.sort()
        : null,
  }
}

function terminalEvent(body: string | null, threadId: string) {
  for (const event of (body ?? '').split(/\r?\n\r?\n/).reverse()) {
    if (!event.includes('RUN_FINISHED') && !event.includes('RUN_ERROR')) continue
    const data = event
      .split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n')
    let frame
    try {
      frame = record(JSON.parse(data))
    } catch {
      continue
    }
    if (frame.threadId != null && frame.threadId !== threadId) continue
    if (frame.type === 'RUN_ERROR') {
      return { terminal: 'error' as const, runId: id(frame.runId), slots: slots(null) }
    }
    if (frame.type !== 'RUN_FINISHED' || frame.threadId !== threadId) continue
    const outcome = record(frame.outcome)
    const terminal: Terminal =
      frame.outcome == null || outcome.type === 'success'
        ? 'success'
        : outcome.type === 'interrupt'
          ? 'interrupt'
          : outcome.type === 'cancelled'
            ? 'cancelled'
            : 'unknown'
    return { terminal, runId: id(frame.runId), slots: slots(outcome.interrupts) }
  }
  return { terminal: 'none' as const, runId: null, slots: slots(null) }
}

function durableSnapshot(body: string | null, threadId: string, requested: boolean) {
  let value: Record<string, unknown> = {}
  if (body !== null) {
    try {
      value = record(JSON.parse(body))
    } catch {
      /* A failed read has no durable evidence. */
    }
  }
  const valid = value.threadId === threadId && Array.isArray(value.interrupts)
  const snapshot = valid ? value : {}
  const pending = slots(snapshot.interrupts)
  const durable: Durable = !requested
    ? 'none'
    : !valid
      ? 'unavailable'
      : id(snapshot.activeRunId)
        ? 'active'
        : id(snapshot.interruptRunId) || snapshot.runStatus === 'interrupted' || pending.count > 0
          ? 'interrupted'
          : 'idle'
  return {
    durable,
    slots: pending,
    runId: id(snapshot.interruptRunId) ?? id(snapshot.activeRunId) ?? id(snapshot.runId),
    hasCheckpoint: id(snapshot.checkpointId) !== null,
  }
}

interface Observation {
  count: number
  request?: Request
  response?: Response
  finished?: boolean
}

async function completedBody(observation: Observation): Promise<string | null> {
  const response = observation.response
  if (!observation.finished || !response?.ok()) return null
  try {
    if (Number(response.headers()['content-length']) > MAX_BODY_BYTES) return null
    const body = await response.body()
    return body.length <= MAX_BODY_BYTES ? body.toString('utf8') : null
  } catch {
    return null
  }
}

async function bounded<T>(work: Promise<T>, fallback: T): Promise<T> {
  let timer: NodeJS.Timeout | undefined
  try {
    return await Promise.race([
      work.catch(() => fallback),
      new Promise<T>((resolve) => {
        timer = setTimeout(() => resolve(fallback), READ_DEADLINE_MS)
      }),
    ])
  } finally {
    clearTimeout(timer)
  }
}

function httpStatus(observation: Observation) {
  const status = observation.response?.status()
  return status !== undefined && status >= 100 && status <= 599 ? status : null
}

/** Capture only counts and agreement from the exact current chat's normal browser requests. */
export function observeApprovalCard(
  page: Page,
  {
    apiBase,
    conversationId,
    agentId,
  }: { apiBase: string; conversationId: string; agentId: string },
) {
  const api = new URL(apiBase).origin
  const chat = api + '/api/conversations/' + encodeURIComponent(conversationId) + '/ag-ui/'
  const grantUrl = api + '/api/agents/' + encodeURIComponent(agentId) + '/tool-approval'
  const post: Observation = { count: 0 }
  const snapshot: Observation = { count: 0 }
  const grant: Observation = { count: 0 }
  function target(request: Request) {
    const url = request.url()
    if (request.method() === 'POST' && url === chat + 'runs') return post
    if (request.method() !== 'GET') return
    if (url === chat + 'snapshot') return snapshot
    if (url === grantUrl) return grant
  }
  function started(request: Request) {
    const seen = target(request)
    if (!seen) return
    seen.count = Math.min(seen.count + 1, LIMIT)
    seen.request = request
    seen.response = undefined
    seen.finished = false
  }
  function headers(response: Response) {
    const seen = target(response.request())
    if (seen?.request === response.request()) seen.response = response
  }
  function finished(request: Request) {
    const seen = target(request)
    if (seen?.request === request) seen.finished = true
  }
  page.on('request', started)
  page.on('response', headers)
  page.on('requestfinished', finished)
  function dispose() {
    page.off('request', started)
    page.off('response', headers)
    page.off('requestfinished', finished)
  }
  return {
    dispose,
    async attachOnFailure(info: TestInfo) {
      dispose()
      try {
        const [streamBody, snapshotBody, approveVisible, grantVisible] = await Promise.all([
          bounded(completedBody(post), null),
          bounded(completedBody(snapshot), null),
          bounded(page.getByTestId('approval-approve-button').last().isVisible(), false),
          bounded(
            page
              .getByRole('button', { name: '이 대화 동안 이 도구는 묻지 않기', exact: true })
              .last()
              .isVisible(),
            false,
          ),
        ])
        const end = terminalEvent(streamBody, conversationId)
        const read = durableSnapshot(snapshotBody, conversationId, snapshot.count > 0)
        const sameSlots =
          end.terminal === 'interrupt' && end.slots.ids !== null && read.slots.ids !== null
            ? end.slots.ids.length === read.slots.ids.length &&
              end.slots.ids.every((slot, i) => slot === read.slots.ids?.[i])
            : null
        await info.attach('approval-card-observations', {
          contentType: 'application/json',
          body: Buffer.from(
            JSON.stringify({
              schemaVersion: 1,
              postCount: post.count,
              postStatus: httpStatus(post),
              terminal: end.terminal,
              terminalSlotCount: end.slots.count,
              terminalApprovalCount: end.slots.approvals,
              snapshotCount: snapshot.count,
              snapshotStatus: httpStatus(snapshot),
              durable: read.durable,
              durableSlotCount: read.slots.count,
              durableApprovalCount: read.slots.approvals,
              durableReadOnlyCount: read.slots.readOnly,
              sameSlots,
              sameRun: end.runId && read.runId ? end.runId === read.runId : null,
              hasCheckpoint: read.hasCheckpoint,
              grantReadStatus: httpStatus(grant),
              approveVisible,
              grantVisible,
            }),
          ),
        })
      } catch {
        /* Preserve the assertion that triggered the observation. */
      }
    },
  }
}
