export type RunActivityKind =
  | 'thinking'
  | 'planning'
  | 'tool'
  | 'subagent'
  | 'background_subagent'
  | 'artifact'
  | 'memory'
  | 'interrupt'
  | 'checkpoint'
  | 'responding'
  | 'reconnecting'
  | 'done'
  | 'error'

export type RunActivityStatus =
  | 'pending'
  | 'running'
  | 'requires_action'
  | 'complete'
  | 'error'
  | 'cancelled'

export interface RunActivity {
  id: string
  runId: string
  kind: RunActivityKind
  status: RunActivityStatus
  title: string
  subtitle?: string
  namespace: string[]
  startedAt?: string
  endedAt?: string
  toolCallId?: string
  parentId?: string
  data?: Record<string, unknown>
}

export interface ProtocolEvent {
  type?: string
  method: string
  params?: {
    namespace?: string[]
    data?: unknown
    timestamp?: string
  }
  seq?: number
  event_id?: string
  run_id?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function namespaceKey(namespace: readonly string[]): string {
  return namespace.length > 0 ? namespace.join('/') : 'root'
}

function eventRunId(event: ProtocolEvent): string {
  return event.run_id ?? event.event_id ?? 'active'
}

function eventData(event: ProtocolEvent): unknown {
  return event.params?.data
}

function eventNamespace(event: ProtocolEvent): string[] {
  return event.params?.namespace ?? []
}

function statusFromValue(value: unknown): RunActivityStatus {
  const raw = textValue(value)?.toLowerCase()
  if (raw === 'success' || raw === 'succeeded' || raw === 'complete' || raw === 'completed') {
    return 'complete'
  }
  if (raw === 'failed' || raw === 'error') return 'error'
  if (raw === 'cancelled' || raw === 'canceled') return 'cancelled'
  if (raw === 'interrupted' || raw === 'requires_action') return 'requires_action'
  return raw === 'pending' ? 'pending' : 'running'
}

function upsertActivity(current: readonly RunActivity[], next: RunActivity): RunActivity[] {
  const index = current.findIndex((item) => item.id === next.id)
  if (index < 0) return [...current, next]
  const copy = [...current]
  copy[index] = { ...copy[index], ...next, data: { ...copy[index].data, ...next.data } }
  return copy
}

function markRunningAsError(current: readonly RunActivity[], event: ProtocolEvent): RunActivity[] {
  const endedAt = event.params?.timestamp
  return current.map((item) =>
    item.status === 'running' || item.status === 'pending'
      ? { ...item, status: 'error', endedAt: item.endedAt ?? endedAt }
      : item,
  )
}

function activityBase(
  event: ProtocolEvent,
  kind: RunActivityKind,
  key: string,
): Pick<RunActivity, 'id' | 'runId' | 'kind' | 'namespace' | 'startedAt'> {
  const namespace = eventNamespace(event)
  const runId = eventRunId(event)
  return {
    id: `${runId}:${kind}:${key}`,
    runId,
    kind,
    namespace,
    startedAt: event.params?.timestamp,
  }
}

function reduceMessages(current: readonly RunActivity[], event: ProtocolEvent): RunActivity[] {
  const data = eventData(event)
  const namespace = eventNamespace(event)
  let next = current
  if (namespace.length > 0) {
    next = upsertActivity(next, {
      ...activityBase(event, 'subagent', namespaceKey(namespace)),
      status: 'running',
      title: namespace.at(-1) ?? 'Subagent',
      parentId:
        namespace.length > 1
          ? `${eventRunId(event)}:subagent:${namespaceKey(namespace.slice(0, -1))}`
          : undefined,
    })
  }
  if (!isRecord(data)) return next

  const toolChunks = asRecords(data.tool_call_chunks)
  for (const chunk of toolChunks) {
    const toolCallId =
      textValue(chunk.id) ?? textValue(chunk.tool_call_id) ?? `seq-${event.seq ?? 0}`
    const name = textValue(chunk.name) ?? textValue(chunk.tool_name) ?? 'Tool'
    next = upsertActivity(next, {
      ...activityBase(event, 'tool', toolCallId),
      status: 'running',
      title: name,
      toolCallId,
      data: { args: chunk.args },
    })
  }

  const content = textValue(data.chunk) ?? textValue(data.content)
  if (content) {
    next = upsertActivity(next, {
      ...activityBase(event, 'responding', namespaceKey(namespace)),
      status: 'running',
      title: 'Responding',
      data: { preview: content },
    })
  }

  const blocks = asRecords(data.content_blocks)
  if (blocks.some((block) => textValue(block.type)?.includes('reasoning'))) {
    next = upsertActivity(next, {
      ...activityBase(event, 'thinking', namespaceKey(namespace)),
      status: 'running',
      title: 'Thinking',
    })
  }
  return next
}

function reduceTools(current: readonly RunActivity[], event: ProtocolEvent): RunActivity[] {
  const data = eventData(event)
  if (!isRecord(data)) return current
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
  if (!isRecord(data)) return current
  let next = current
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
  if (!isRecord(data)) return current
  const namespace = eventNamespace(event)
  const id = textValue(data.trigger_call_id) ?? textValue(data.id) ?? namespaceKey(namespace)
  const status = statusFromValue(data.status ?? data.event)
  return upsertActivity(current, {
    ...activityBase(event, 'subagent', id),
    status,
    title: textValue(data.name) ?? namespace.at(-1) ?? 'Subagent',
    toolCallId: textValue(data.trigger_call_id),
    data,
  })
}

function reduceCustom(current: readonly RunActivity[], event: ProtocolEvent): RunActivity[] {
  const data = eventData(event)
  const name = event.method.startsWith('custom:')
    ? event.method.slice(7)
    : isRecord(data)
      ? textValue(data.name)
      : undefined
  if (name === 'artifact' || name === 'file') {
    return upsertActivity(current, {
      ...activityBase(event, 'artifact', name),
      status: 'running',
      title: 'Artifact',
      data: isRecord(data) ? data : { payload: data },
    })
  }
  if (name === 'memory') {
    return upsertActivity(current, {
      ...activityBase(event, 'memory', name),
      status: 'running',
      title: 'Memory',
      data: isRecord(data) ? data : { payload: data },
    })
  }
  if (name === 'stale' || name === 'reconnect') {
    return upsertActivity(current, {
      ...activityBase(event, 'reconnecting', name),
      status: name === 'stale' ? 'error' : 'running',
      title: 'Reconnecting',
      data: isRecord(data) ? data : { payload: data },
    })
  }
  return current
}

export function reduceActivity(
  current: readonly RunActivity[],
  event: ProtocolEvent,
): RunActivity[] {
  if (event.method === 'error') {
    return upsertActivity(markRunningAsError(current, event), {
      ...activityBase(event, 'error', 'stream'),
      status: 'error',
      title: 'Error',
      data: isRecord(eventData(event)) ? eventData(event) : { payload: eventData(event) },
    })
  }
  if (event.method === 'messages') return reduceMessages(current, event)
  if (event.method === 'tools') return reduceTools(current, event)
  if (event.method === 'updates' || event.method === 'values') return reduceState(current, event)
  if (event.method === 'tasks' || event.method === 'lifecycle')
    return reduceLifecycle(current, event)
  if (event.method === 'checkpoints') {
    return upsertActivity(current, {
      ...activityBase(event, 'checkpoint', String(event.seq ?? 'latest')),
      status: 'complete',
      title: 'Checkpoint',
      data: isRecord(eventData(event)) ? eventData(event) : { payload: eventData(event) },
    })
  }
  if (event.method === 'custom' || event.method.startsWith('custom:')) {
    return reduceCustom(current, event)
  }
  return current
}
