import type {
  ProtocolEvent,
  RunActivity,
  RunActivityKind,
  RunActivityStatus,
} from './activity-types'

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function asRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

export function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

export function namespaceKey(namespace: readonly string[]): string {
  return namespace.length > 0 ? namespace.join('/') : 'root'
}

export function eventRunId(event: ProtocolEvent): string {
  return event.run_id ?? event.event_id ?? 'active'
}

export function eventData(event: ProtocolEvent): unknown {
  return event.params?.data
}

export function dataRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : { payload: value }
}

export function eventNamespace(event: ProtocolEvent): readonly string[] {
  return event.params?.namespace ?? []
}

export function statusFromValue(value: unknown): RunActivityStatus {
  const raw = textValue(value)?.toLowerCase()
  if (
    raw === 'success' ||
    raw === 'succeeded' ||
    raw === 'complete' ||
    raw === 'completed' ||
    raw === 'done' ||
    raw === 'finished'
  ) {
    return 'complete'
  }
  if (raw === 'failed' || raw === 'error') return 'error'
  if (raw === 'cancelled' || raw === 'canceled') return 'cancelled'
  if (raw === 'interrupted' || raw === 'requires_action') return 'requires_action'
  return raw === 'pending' ? 'pending' : 'running'
}

export function isTerminalStatus(status: RunActivityStatus): boolean {
  return status === 'complete' || status === 'error' || status === 'cancelled'
}

export function upsertActivity(current: readonly RunActivity[], next: RunActivity): RunActivity[] {
  const index = current.findIndex((item) => item.id === next.id)
  if (index < 0) return [...current, next]
  const copy = [...current]
  copy[index] = { ...copy[index], ...next, data: { ...copy[index].data, ...next.data } }
  return copy
}

export function markRunningAsStatus(
  current: readonly RunActivity[],
  event: ProtocolEvent,
  status: RunActivityStatus,
): RunActivity[] {
  const endedAt = event.params?.timestamp
  return current.map((item) =>
    item.status === 'running' || item.status === 'pending'
      ? { ...item, status, endedAt: item.endedAt ?? endedAt }
      : item,
  )
}

export function activityBase(
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
