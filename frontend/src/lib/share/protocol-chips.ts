import {
  asRecord,
  asRecords,
  chip,
  planMeta,
  resultMeta,
  subagentTitle,
  text,
  type ChipInfo,
  type ChipStatus,
  type OrderedChip,
} from './chip-values'
import type { ProtocolTraceEvent, TraceEvent } from '@/lib/types/share'

interface ProtocolToolCall {
  order: number
  name: string
  args: Record<string, unknown>
  status: ChipStatus
  result?: unknown
}

type ProtocolToolPatch = {
  name?: string | undefined
  args?: Record<string, unknown> | undefined
  status?: ChipStatus | undefined
  result?: unknown
}

const _isProtocolEvent = (evt: TraceEvent): evt is ProtocolTraceEvent =>
  'method' in evt && typeof evt.method === 'string'

const _protocolData = (evt: ProtocolTraceEvent): unknown => evt.params?.data ?? evt.data

function _protocolNamespace(evt: ProtocolTraceEvent): string[] {
  const namespace = evt.params?.namespace ?? evt.namespace
  return Array.isArray(namespace)
    ? namespace.filter((item): item is string => typeof item === 'string')
    : []
}

function _protocolKey(evt: ProtocolTraceEvent, order: number): string {
  return (
    text(evt.event_id) ?? text(evt.upstream_event_id) ?? text(evt.id) ?? `seq-${evt.seq ?? order}`
  )
}

function _protocolStatus(data: Record<string, unknown>): ChipStatus {
  const raw = text(data.status ?? data.event ?? data.state)?.toLowerCase()
  if (raw === 'tool-error' || raw === 'error' || raw === 'failed' || raw === 'failure')
    return 'error'
  if (raw === 'cancelled' || raw === 'canceled') return 'cancelled'
  return 'success'
}

function _isToolBlock(record: Record<string, unknown>): boolean {
  const type = text(record.type)
  return (
    type === 'tool_call' ||
    type === 'tool_call_chunk' ||
    type === 'server_tool_call' ||
    type === 'server_tool_call_chunk'
  )
}

function _messageToolChunks(data: unknown): Record<string, unknown>[] {
  const record = asRecord(data)
  const chunks = [...asRecords(record.tool_call_chunks), ...asRecords(record.tool_calls)]
  const content = asRecord(record.content)
  if (_isToolBlock(content)) chunks.push(content)
  const delta = asRecord(record.delta)
  const fields = asRecord(delta.fields)
  if (text(delta.type) === 'block-delta' && _isToolBlock(fields)) chunks.push(fields)
  return chunks
}

function _upsertToolCall(
  calls: Map<string, ProtocolToolCall>,
  id: string,
  order: number,
  patch: ProtocolToolPatch,
): void {
  const current = calls.get(id)
  const status =
    patch.status === 'error' || patch.status === 'cancelled'
      ? patch.status
      : (current?.status ?? patch.status ?? 'success')
  calls.set(id, {
    order: current?.order ?? order,
    name: patch.name ?? current?.name ?? 'Tool',
    args: patch.args ?? current?.args ?? {},
    status,
    result: patch.result ?? current?.result,
  })
}

function _addMessageTools(
  calls: Map<string, ProtocolToolCall>,
  evt: ProtocolTraceEvent,
  order: number,
): void {
  for (const chunk of _messageToolChunks(_protocolData(evt))) {
    const id = text(chunk.id) ?? text(chunk.tool_call_id) ?? _protocolKey(evt, order)
    _upsertToolCall(calls, id, order, {
      name: text(chunk.name) ?? text(chunk.tool_name),
      args: asRecord(chunk.args ?? chunk.parameters),
    })
  }
}

function _addToolsEvent(
  calls: Map<string, ProtocolToolCall>,
  evt: ProtocolTraceEvent,
  order: number,
): void {
  const data = asRecord(_protocolData(evt))
  const id = text(data.tool_call_id) ?? text(data.id) ?? _protocolKey(evt, order)
  _upsertToolCall(calls, id, order, {
    name: text(data.tool_name) ?? text(data.name),
    status: _protocolStatus(data),
    result: data.output ?? data.result,
  })
}

function _subagentFromProtocol(evt: ProtocolTraceEvent, order: number): OrderedChip | null {
  if (!['lifecycle', 'tasks', 'subagents'].includes(evt.method)) return null
  const data = asRecord(_protocolData(evt))
  const namespace = _protocolNamespace(evt)
  const title =
    text(data.name) ??
    text(data.graph_name) ??
    text(data.agent_name) ??
    text(data.subagent_type) ??
    namespace.at(-1)
  if (!title) return null
  return chip('subagent', _protocolStatus(data), title, order)
}

function _customEventName(evt: ProtocolTraceEvent): string | undefined {
  const data = asRecord(_protocolData(evt))
  const name = evt.method.startsWith('custom:')
    ? evt.method.slice(7)
    : evt.method === 'custom'
      ? (text(data.name) ?? text(data.channel))
      : undefined
  return name?.startsWith('moldy.') ? name.slice('moldy.'.length) : name
}

function _customChip(evt: ProtocolTraceEvent, order: number): OrderedChip | null {
  const name = _customEventName(evt)
  if (!name) return null
  const data = _protocolData(evt)
  const dataRecord = asRecord(data)
  const payload = asRecord('payload' in dataRecord ? dataRecord.payload : data)
  if (name === 'artifact' || name === 'file' || name === 'file_event') {
    const title = text(payload.display_name) ?? text(payload.path) ?? text(payload.id) ?? name
    return chip('tool', 'success', title, order, name)
  }
  if (name === 'memory' || name.startsWith('memory_')) {
    return chip('tool', 'success', name, order)
  }
  return null
}

function _protocolToolChip(call: ProtocolToolCall): OrderedChip {
  if (call.name === 'task') {
    return chip('subagent', call.status, subagentTitle(call.args), call.order)
  }
  if (call.name === 'write_todos') {
    return chip('tool', call.status, 'Plan', call.order, planMeta(call.args))
  }
  return chip('tool', call.status, call.name, call.order, resultMeta(call.result))
}

export function protocolChips(events: TraceEvent[]): ChipInfo[] {
  const direct: OrderedChip[] = []
  const tools = new Map<string, ProtocolToolCall>()
  const seenSubagents = new Set<string>()

  events.forEach((evt, order) => {
    if (!_isProtocolEvent(evt)) return
    if (evt.method === 'messages') _addMessageTools(tools, evt, order)
    if (evt.method === 'tools') _addToolsEvent(tools, evt, order)
    const subagent = _subagentFromProtocol(evt, order)
    if (subagent) {
      const key = `${subagent.title}:${_protocolNamespace(evt).join('/')}`
      if (!seenSubagents.has(key)) {
        seenSubagents.add(key)
        direct.push(subagent)
      }
    }
    const custom = _customChip(evt, order)
    if (custom) direct.push(custom)
  })

  return [...direct, ...Array.from(tools.values()).map(_protocolToolChip)]
    .sort((a, b) => a.order - b.order)
    .map(({ kind, status, title, meta }) =>
      meta ? { kind, status, title, meta } : { kind, status, title },
    )
}
