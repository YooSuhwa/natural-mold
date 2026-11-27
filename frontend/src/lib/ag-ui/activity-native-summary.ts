import type {
  RunActivity,
  RunActivityKind,
  RunActivityStatus,
} from '@/lib/chat/langgraph-runtime/activity-types'

type Entries = readonly RunActivity[]

function key(runId: string, kind: RunActivityKind, entity: string, lane?: string): string {
  return JSON.stringify([runId, kind, lane ?? null, entity])
}

function identifier(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function timestamp(value: unknown): string | undefined {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) return
  const time = new Date(value)
  return Number.isFinite(time.getTime()) ? time.toISOString() : undefined
}

function open(activity: RunActivity): boolean {
  return activity.status === 'running' || activity.status === 'pending'
}

function lanePath(current: Entries, runId: string, lane?: string): string[] | undefined {
  if (!lane) return []
  const owner = current.find((item) => item.id === key(runId, 'subagent', lane))
  return owner && [...owner.namespace, lane]
}

function start(current: Entries, activity: RunActivity): Entries {
  return current.some((item) => item.id === activity.id) ? current : [...current, activity]
}

function settle(
  current: Entries,
  id: string,
  status: RunActivityStatus,
  endedAt?: string,
): Entries {
  const index = current.findIndex((item) => item.id === id && open(item))
  if (index < 0) return current
  const next = [...current]
  next[index] = { ...current[index], status, endedAt }
  return next
}

function outcomeStatus(failed: boolean, outcome: Record<string, unknown>): RunActivityStatus {
  if (failed) return 'error'
  if (outcome.type === 'cancelled') return 'cancelled'
  if (outcome.type === 'interrupt' || outcome.type === 'suspended') return 'requires_action'
  return 'complete'
}

function terminalId(runId: string): string {
  return key(runId, 'done', 'terminal')
}

const nativeKinds = new Set<RunActivityKind>([
  'responding',
  'thinking',
  'tool',
  'subagent',
  'interrupt',
])

function physicalKey(activity: RunActivity, provisional: string, physical: string) {
  if (activity.runId !== provisional) return
  if (activity.id === terminalId(provisional)) return terminalId(physical)
  if (!nativeKinds.has(activity.kind)) return
  // Only this reducer's canonical UI tuple may be rewritten. Treat its lane and
  // entity as opaque values, even if either contains the provisional run ID.
  let tuple: unknown
  try {
    tuple = JSON.parse(activity.id)
  } catch {
    return
  }
  if (!Array.isArray(tuple) || tuple.length !== 4) return
  const [run, kind, lane, entity] = tuple as unknown[]
  if (run !== provisional || kind !== activity.kind || typeof entity !== 'string') return
  if (lane !== null && typeof lane !== 'string') return
  if (activity.id !== key(provisional, kind, entity, lane ?? undefined)) return
  return key(physical, kind, entity, lane ?? undefined)
}

/** A verified receipt binds a locally observed submission to its physical run.
 * This rekeys presentation rows only; it cannot produce a run or a credential. */
export function rebindAgUiNativeActivities(
  current: Entries,
  provisional: string,
  physical: string,
): Entries {
  if (!provisional || !physical || provisional === physical) return current
  const ids = new Map<string, string>()
  for (const item of current) {
    const target = physicalKey(item, provisional, physical)
    if (target) ids.set(item.id, target)
  }
  if (!ids.size) return current
  const retained = new Map(
    current.filter((item) => item.runId === physical).map((item) => [item.id, item]),
  )
  const supplemented = new Map<string, RunActivity>()
  const result: RunActivity[] = []
  for (const item of current) {
    const id = item.runId === provisional ? ids.get(item.id) : undefined
    if (!id) {
      result.push(item)
      continue
    }
    const existing = retained.get(id)
    if (existing) {
      // A snapshot may already contain the start, while the stream observed its end.
      // A terminal physical row always wins over an older open observation.
      if (open(existing) && !open(item)) {
        supplemented.set(id, {
          ...existing,
          status: item.status,
          endedAt: item.endedAt,
          startedAt: existing.startedAt ?? item.startedAt,
        })
      }
      continue
    }
    result.push({
      ...item,
      id,
      runId: physical,
      ...(item.parentId && { parentId: ids.get(item.parentId) ?? item.parentId }),
    })
  }
  return supplemented.size
    ? result.map((item) => (item.runId === physical ? (supplemented.get(item.id) ?? item) : item))
    : result
}

function finishRun(current: Entries, event: Record<string, unknown>, runId: string): Entries {
  const id = terminalId(runId)
  if (current.some((item) => item.id === id)) return current
  const outcome = object(event.outcome) ? event.outcome : {}
  const status = outcomeStatus(event.type === 'RUN_ERROR', outcome)
  const end = timestamp(event.timestamp)
  const pending = new Set(
    Array.isArray(outcome.pendingToolCallIds) ? outcome.pendingToolCallIds : [],
  )
  const interrupts = Array.isArray(outcome.interrupts) ? outcome.interrupts.filter(object) : []
  for (const interrupt of interrupts) {
    if (typeof interrupt.toolCallId === 'string') pending.add(interrupt.toolCallId)
  }
  const next: RunActivity[] = current.map((item) => {
    if (item.runId !== runId || !open(item)) return item
    let closed = status
    // An unanswered call is pending frontend input even on a successful run.
    if (item.kind === 'tool' && (pending.has(item.toolCallId) || status === 'complete')) {
      closed = 'requires_action'
    }
    return { ...item, status: closed, endedAt: end }
  })
  for (const interrupt of interrupts) {
    const slot = identifier(interrupt.id)
    const lane = identifier(interrupt.subagentRunId)
    const namespace = lanePath(current, runId, lane)
    if (!slot || !namespace) continue
    next.push({
      id: key(runId, 'interrupt', slot, lane),
      runId,
      namespace,
      kind: 'interrupt',
      title: 'interrupt',
      status: 'requires_action',
      startedAt: end,
      endedAt: end,
      toolCallId: identifier(interrupt.toolCallId),
    })
  }
  const kind = status === 'error' ? 'error' : status === 'requires_action' ? 'interrupt' : 'done'
  const terminalRunStatus =
    status === 'error'
      ? 'failed'
      : status === 'cancelled'
        ? 'canceled'
        : status === 'requires_action'
          ? 'interrupted'
          : 'completed'
  next.push({
    id,
    runId,
    kind,
    namespace: [],
    title: kind,
    status,
    terminalRunStatus,
    startedAt: end,
    endedAt: end,
  })
  return next
}

/** Only public lifecycle, name and identity fields enter the summary. Tool arguments,
 * replies, reasoning, interrupt payloads and execution traces remain outside it. */
export function reduceAgUiNativeActivities(
  current: Entries,
  event: Record<string, unknown>,
  runId: string,
): Entries {
  const lane = identifier(event.subagentRunId)
  switch (event.type) {
    case 'TEXT_MESSAGE_START':
    case 'TEXT_MESSAGE_END': {
      const messageId = identifier(event.messageId)
      if (!messageId || (event.role && event.role !== 'assistant')) return current
      const id = key(runId, 'responding', messageId, lane)
      const time = timestamp(event.timestamp)
      if (event.type === 'TEXT_MESSAGE_END') return settle(current, id, 'complete', time)
      const namespace = lanePath(current, runId, lane)
      return namespace
        ? start(current, {
            id,
            runId,
            namespace,
            kind: 'responding',
            title: 'responding',
            status: 'running',
            startedAt: time,
          })
        : current
    }
    case 'REASONING_START':
    case 'REASONING_END': {
      const id = identifier(event.messageId)
      if (!id) return current
      const entity = key(runId, 'thinking', id, lane)
      const time = timestamp(event.timestamp)
      if (event.type === 'REASONING_END') return settle(current, entity, 'complete', time)
      const namespace = lanePath(current, runId, lane)
      return namespace
        ? start(current, {
            id: entity,
            runId,
            namespace,
            kind: 'thinking',
            title: 'thinking',
            status: 'running',
            startedAt: time,
          })
        : current
    }
    case 'TOOL_CALL_START': {
      const toolCallId = identifier(event.toolCallId)
      const title = identifier(event.toolCallName)
      const namespace = lanePath(current, runId, lane)
      if (!toolCallId || !title || !namespace) return current
      return start(current, {
        id: key(runId, 'tool', toolCallId, lane),
        runId,
        namespace,
        kind: 'tool',
        toolCallId,
        title,
        status: 'running',
        startedAt: timestamp(event.timestamp),
      })
    }
    case 'TOOL_CALL_RESULT': {
      const tool = identifier(event.toolCallId)
      if (!tool) return current
      const meta =
        object(event.metadata) && object(event.metadata.moldy) ? event.metadata.moldy : undefined
      const candidates = current.filter(
        (item) => item.runId === runId && item.kind === 'tool' && item.toolCallId === tool,
      )
      const owner =
        candidates.find((item) => item.id === key(runId, 'tool', tool, lane)) ??
        (candidates.length === 1 ? candidates[0] : undefined)
      return owner
        ? settle(
            current,
            owner.id,
            meta?.isError === true ? 'error' : 'complete',
            timestamp(event.timestamp),
          )
        : current
    }
    case 'SUBAGENT_STARTED': {
      const title = identifier(event.name)
      const parent = identifier(event.parentSubagentRunId)
      const namespace = lanePath(current, runId, parent)
      if (!lane || !title || !namespace || namespace.includes(lane)) return current
      return start(current, {
        id: key(runId, 'subagent', lane),
        runId,
        namespace,
        kind: 'subagent',
        title,
        parentId: parent ? key(runId, 'subagent', parent) : undefined,
        toolCallId: identifier(event.parentToolCallId),
        status: 'running',
        startedAt: timestamp(event.timestamp),
      })
    }
    case 'SUBAGENT_ERROR':
    case 'SUBAGENT_FINISHED': {
      if (!lane) return current
      const outcome = object(event.outcome) ? event.outcome : {}
      const status =
        event.type === 'SUBAGENT_ERROR' && event.code === 'CANCELLED'
          ? 'cancelled'
          : outcomeStatus(event.type === 'SUBAGENT_ERROR', outcome)
      return settle(current, key(runId, 'subagent', lane), status, timestamp(event.timestamp))
    }
    case 'RUN_FINISHED':
    case 'RUN_ERROR':
      return lane ? current : finishRun(current, event, runId)
    default:
      // In particular, TOOL_CALL_END only closes the arguments, and text/tool deltas
      // must not re-render the summary for each token.
      return current
  }
}

/** Keep active indicators last for the existing live summary. A confirmed terminal
 * event closes transient custom indicators that do not carry their own final state. */
export function mergeAgUiRunActivities(
  native: Entries,
  custom: Entries,
  runId: string,
): RunActivity[] {
  const active = native.filter((item) => item.runId === runId)
  const terminal = active.find((item) => item.id === terminalId(runId))
  const extras = custom.filter((item) => item.runId === runId)
  if (terminal)
    return [
      ...active.filter((item) => item !== terminal),
      ...extras.map((item) =>
        open(item) ? { ...item, status: terminal.status, endedAt: terminal.endedAt } : item,
      ),
      terminal,
    ]
  const items = [...active, ...extras]
  return [...items.filter((item) => !open(item)), ...items.filter(open)]
}
