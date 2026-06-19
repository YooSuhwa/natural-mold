import {
  activityBase,
  asRecords,
  dataRecord,
  eventData,
  eventNamespace,
  eventRunId,
  isRecord,
  isTerminalStatus,
  markRunningAsStatus,
  namespaceKey,
  statusFromValue,
  textValue,
  upsertActivity,
} from './activity-state'
import type { ProtocolEvent, RunActivity } from './activity-types'

export type {
  ProtocolEvent,
  RunActivity,
  RunActivityKind,
  RunActivityStatus,
} from './activity-types'

function reduceMessages(current: readonly RunActivity[], event: ProtocolEvent): RunActivity[] {
  const data = eventData(event)
  const namespace = eventNamespace(event)
  let next: RunActivity[] = [...current]
  if (namespace.length > 0) {
    const subagentKey = subagentKeyFromNamespace(namespace) ?? namespaceKey(namespace)
    next = upsertActivity(next, {
      ...activityBase(event, 'subagent', subagentKey),
      status: 'running',
      title: subagentTitleFromNamespace(namespace),
      parentId: parentSubagentId(event, namespace),
    })
  }
  if (!isRecord(data)) return next

  const toolChunks = asRecords(data.tool_call_chunks)
  for (const chunk of toolChunks) {
    const name = textValue(chunk.name) ?? textValue(chunk.tool_name) ?? 'Tool'
    const chunkIndex =
      typeof chunk.index === 'number' && Number.isFinite(chunk.index) ? chunk.index : undefined
    const toolCallId =
      textValue(chunk.id) ??
      textValue(chunk.tool_call_id) ??
      continuationToolCallId(next, name, namespace, chunkIndex)
    if (!toolCallId) continue
    next = upsertActivity(next, {
      ...activityBase(event, 'tool', toolCallId),
      status: 'running',
      title: name,
      toolCallId,
      data: {
        args: chunk.args,
        ...(chunkIndex === undefined ? {} : { index: chunkIndex }),
      },
    })
  }

  const content = textValue(data.chunk) ?? textValue(data.content)
  if (content) {
    next = upsertActivity(next, {
      ...streamingStatusActivityBase(next, event, 'responding'),
      status: 'running',
      title: 'Responding',
      data: { preview: content },
    })
  }

  const blocks = asRecords(data.content_blocks)
  if (blocks.some((block) => textValue(block.type)?.includes('reasoning'))) {
    next = upsertActivity(next, {
      ...streamingStatusActivityBase(next, event, 'thinking'),
      status: 'running',
      title: 'Thinking',
    })
  }
  return next
}

function streamingStatusActivityBase(
  current: readonly RunActivity[],
  event: ProtocolEvent,
  kind: 'responding' | 'thinking',
): Pick<RunActivity, 'id' | 'runId' | 'kind' | 'namespace' | 'startedAt'> {
  const namespace = eventNamespace(event)
  const existing = current.find(
    (item) =>
      item.kind === kind &&
      item.status === 'running' &&
      namespaceKey(item.namespace) === namespaceKey(namespace),
  )
  if (!existing) return activityBase(event, kind, namespaceKey(namespace))
  return {
    id: existing.id,
    runId: existing.runId,
    kind,
    namespace,
    startedAt: existing.startedAt ?? event.params?.timestamp,
  }
}

function reduceTools(current: readonly RunActivity[], event: ProtocolEvent): RunActivity[] {
  const data = eventData(event)
  if (!isRecord(data)) return [...current]
  const toolCallId = textValue(data.tool_call_id) ?? textValue(data.id) ?? `seq-${event.seq ?? 0}`
  const name = textValue(data.tool_name) ?? textValue(data.name) ?? 'Tool'
  const status = statusFromValue(data.status ?? data.event)
  return upsertActivity(current, {
    ...activityBase(event, 'tool', toolCallId),
    status,
    title: name,
    toolCallId,
    endedAt: status === 'complete' || status === 'error' ? event.params?.timestamp : undefined,
    data,
  })
}

function reduceState(current: readonly RunActivity[], event: ProtocolEvent): RunActivity[] {
  const data = eventData(event)
  if (!isRecord(data)) return [...current]
  let next: RunActivity[] = [...current]
  if (Array.isArray(data.todos)) {
    const todos = asRecords(data.todos)
    const incomplete = todos.some((todo) => {
      const status = textValue(todo.status)?.toLowerCase()
      return status !== 'done' && status !== 'completed' && status !== 'complete'
    })
    next = upsertActivity(next, {
      ...activityBase(event, 'planning', 'todos'),
      status: incomplete ? 'running' : 'complete',
      title: 'Planning',
      data: { todos },
    })
  }
  if ('__interrupt__' in data) {
    next = upsertActivity(next, {
      ...activityBase(event, 'interrupt', 'interrupt'),
      status: 'requires_action',
      title: 'Needs approval',
      data,
    })
  }
  const asyncTasks = Array.isArray(data.async_tasks)
    ? data.async_tasks
    : Object.values(data.async_tasks ?? {})
  for (const task of asRecords(asyncTasks)) {
    const taskId = textValue(task.id) ?? textValue(task.task_id) ?? `seq-${event.seq ?? 0}`
    next = upsertActivity(next, {
      ...activityBase(event, 'background_subagent', taskId),
      status: statusFromValue(task.status),
      title: textValue(task.name) ?? 'Background task',
      data: task,
    })
  }
  return next
}

function reduceLifecycle(current: readonly RunActivity[], event: ProtocolEvent): RunActivity[] {
  const data = eventData(event)
  if (!isRecord(data)) return [...current]
  const namespace = eventNamespace(event)
  const id =
    textValue(data.trigger_call_id) ??
    textValue(data.id) ??
    subagentKeyFromNamespace(namespace) ??
    namespaceKey(namespace)
  const status = statusFromValue(data.status ?? data.event)
  if (namespace.length === 0 && isTerminalStatus(status)) {
    const closed = markRunningAsStatus(current, event, status)
    if (status !== 'complete') return closed
    return upsertActivity(closed, {
      ...activityBase(event, 'done', 'run'),
      status,
      title: 'Done',
      data,
    })
  }
  return upsertActivity(current, {
    ...activityBase(event, 'subagent', id),
    status,
    title: textValue(data.name) ?? namespace.at(-1) ?? 'Subagent',
    toolCallId: textValue(data.trigger_call_id),
    data,
  })
}

function continuationToolCallId(
  activities: readonly RunActivity[],
  name: string,
  namespace: readonly string[],
  chunkIndex: number | undefined,
): string | undefined {
  const candidates = activities.filter((item) => {
    if (
      item.kind !== 'tool' ||
      item.status !== 'running' ||
      item.title !== name ||
      namespaceKey(item.namespace) !== namespaceKey(namespace)
    ) {
      return false
    }
    return chunkIndex === undefined || (isRecord(item.data) && item.data.index === chunkIndex)
  })
  if (chunkIndex === undefined && candidates.length !== 1) return undefined
  return candidates.at(-1)?.toolCallId
}

function subagentKeyFromNamespace(namespace: readonly string[]): string | undefined {
  const tail = namespace.at(-1)
  if (!tail) return undefined
  const separator = tail.indexOf(':')
  return separator > 0 ? tail.slice(separator + 1) : tail
}

function subagentTitleFromNamespace(namespace: readonly string[]): string {
  return subagentKeyFromNamespace(namespace) ?? namespace.at(-1) ?? 'Subagent'
}

function parentSubagentId(event: ProtocolEvent, namespace: readonly string[]): string | undefined {
  if (namespace.length <= 1) return undefined
  const parentNamespace = namespace.slice(0, -1)
  return `${eventRunId(event)}:subagent:${subagentKeyFromNamespace(parentNamespace) ?? namespaceKey(parentNamespace)}`
}

function terminalCustomStatus(name: string | undefined, payload: unknown): RunActivity['status'] {
  const rawStatus = isRecord(payload)
    ? (payload.status ?? payload.event ?? payload.op ?? payload.memory_event)
    : undefined
  const status = statusFromValue(rawStatus)
  if (isTerminalStatus(status) || status === 'requires_action') return status
  if (name === 'memory_proposed') return 'requires_action'
  if (name?.startsWith('memory_')) return 'complete'
  if (name === 'artifact' || name === 'file' || name === 'file_event') return 'complete'
  if (name === 'reconnect') return 'complete'
  return status
}

function reduceCustom(current: readonly RunActivity[], event: ProtocolEvent): RunActivity[] {
  const data = eventData(event)
  const name = event.method.startsWith('custom:')
    ? event.method.slice(7)
    : isRecord(data)
      ? textValue(data.name)
      : undefined
  const payload = isRecord(data) && isRecord(data.payload) ? data.payload : data
  if (name === 'artifact' || name === 'file' || name === 'file_event') {
    return upsertActivity(current, {
      ...activityBase(event, 'artifact', name),
      status: terminalCustomStatus(name, payload),
      title: 'Artifact',
      data: dataRecord(payload),
    })
  }
  if (name === 'memory' || name?.startsWith('memory_')) {
    return upsertActivity(current, {
      ...activityBase(event, 'memory', name),
      status: terminalCustomStatus(name, payload),
      title: 'Memory',
      data: dataRecord(payload),
    })
  }
  if (name === 'stale' || name === 'reconnect') {
    return upsertActivity(current, {
      ...activityBase(event, 'reconnecting', name),
      status: name === 'stale' ? 'error' : terminalCustomStatus(name, payload),
      title: 'Reconnecting',
      data: isRecord(data) ? data : { payload: data },
    })
  }
  return [...current]
}

export function reduceActivity(
  current: readonly RunActivity[],
  event: ProtocolEvent,
): RunActivity[] {
  if (event.method === 'error') {
    const data = eventData(event)
    return upsertActivity(markRunningAsStatus(current, event, 'error'), {
      ...activityBase(event, 'error', 'stream'),
      status: 'error',
      title: 'Error',
      data: dataRecord(data),
    })
  }
  if (event.method === 'messages') return reduceMessages(current, event)
  if (event.method === 'tools') return reduceTools(current, event)
  if (event.method === 'updates' || event.method === 'values') return reduceState(current, event)
  if (event.method === 'tasks' || event.method === 'lifecycle')
    return reduceLifecycle(current, event)
  if (event.method === 'checkpoints') {
    const data = eventData(event)
    return upsertActivity(current, {
      ...activityBase(event, 'checkpoint', String(event.seq ?? 'latest')),
      status: 'complete',
      title: 'Checkpoint',
      data: dataRecord(data),
    })
  }
  if (event.method === 'custom' || event.method.startsWith('custom:')) {
    return reduceCustom(current, event)
  }
  return [...current]
}
