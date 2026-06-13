import { reduceActivity, type ProtocolEvent, type RunActivity } from './activity-model'

export type ActivityProtocolMethod =
  | 'messages'
  | 'tools'
  | 'values'
  | 'updates'
  | 'lifecycle'
  | 'tasks'
  | 'checkpoints'
  | 'custom'
  | `custom:${string}`
  | 'input.requested'
  | 'error'

export interface ActivityProtocolEvent {
  readonly type?: string
  readonly method: ActivityProtocolMethod
  readonly params?: {
    readonly namespace?: readonly string[]
    readonly timestamp?: string | number
    readonly data?: unknown
  }
  readonly seq?: number
  readonly event_id?: string
  readonly run_id?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function eventData(event: ActivityProtocolEvent): unknown {
  return event.params?.data
}

function eventNamespace(event: ActivityProtocolEvent): string[] {
  return Array.isArray(event.params?.namespace)
    ? event.params.namespace.filter((item): item is string => typeof item === 'string')
    : []
}

function eventTimestamp(event: ActivityProtocolEvent): string | undefined {
  const value = event.params?.timestamp
  if (typeof value === 'string') return value
  if (typeof value === 'number') return new Date(value).toISOString()
  return undefined
}

function toActivityEvent(
  event: ActivityProtocolEvent,
  method: ProtocolEvent['method'],
  data: unknown,
): ProtocolEvent {
  return {
    method,
    event_id: event.event_id,
    run_id: event.run_id,
    seq: event.seq,
    params: {
      namespace: eventNamespace(event),
      timestamp: eventTimestamp(event),
      data,
    },
  }
}

function isToolBlock(type: string | undefined): boolean {
  return (
    type === 'tool_call' ||
    type === 'tool_call_chunk' ||
    type === 'server_tool_call' ||
    type === 'server_tool_call_chunk'
  )
}

function reduceToolContent(
  current: readonly RunActivity[],
  event: ActivityProtocolEvent,
  content: Record<string, unknown>,
): RunActivity[] {
  return reduceActivity(
    current,
    toActivityEvent(event, 'messages', {
      tool_call_chunks: [
        {
          id: textValue(content.id),
          name: textValue(content.name),
          args: content.args,
        },
      ],
    }),
  )
}

function reduceContentBlock(
  current: readonly RunActivity[],
  event: ActivityProtocolEvent,
  content: unknown,
): RunActivity[] {
  if (!isRecord(content)) return [...current]
  const type = textValue(content.type)
  if (isToolBlock(type)) return reduceToolContent(current, event, content)
  if (type === 'reasoning') {
    return reduceActivity(
      current,
      toActivityEvent(event, 'messages', { content_blocks: [content] }),
    )
  }
  if (type === 'text') {
    return reduceActivity(current, toActivityEvent(event, 'messages', { chunk: content.text }))
  }
  if (type === 'file') {
    return reduceActivity(
      current,
      toActivityEvent(event, 'custom', { ...content, name: 'artifact' }),
    )
  }
  return reduceActivity(current, toActivityEvent(event, 'messages', { content_blocks: [content] }))
}

function reduceContentDelta(
  current: readonly RunActivity[],
  event: ActivityProtocolEvent,
  delta: unknown,
): RunActivity[] {
  if (!isRecord(delta)) return [...current]
  const type = textValue(delta.type)
  if (type === 'text-delta') {
    return reduceActivity(current, toActivityEvent(event, 'messages', { chunk: delta.text }))
  }
  if (type === 'reasoning-delta') {
    return reduceActivity(
      current,
      toActivityEvent(event, 'messages', { content_blocks: [{ type: 'reasoning' }] }),
    )
  }
  if (
    type === 'block-delta' &&
    isRecord(delta.fields) &&
    isToolBlock(textValue(delta.fields.type))
  ) {
    return reduceToolContent(current, event, delta.fields)
  }
  return [...current]
}

function reduceMessagesEvent(
  current: readonly RunActivity[],
  event: ActivityProtocolEvent,
): RunActivity[] {
  const data = eventData(event)
  if (!isRecord(data)) return reduceActivity(current, toActivityEvent(event, 'messages', data))
  const eventName = textValue(data.event)
  if (eventName === 'content-block-start' || eventName === 'content-block-finish') {
    return reduceContentBlock(current, event, data.content)
  }
  if (eventName === 'content-block-delta') return reduceContentDelta(current, event, data.delta)
  if (eventName === 'error') return reduceActivity(current, toActivityEvent(event, 'error', data))
  return reduceActivity(current, toActivityEvent(event, 'messages', data))
}

function knownToolTitle(current: readonly RunActivity[], toolCallId: string | undefined): string {
  if (!toolCallId) return 'Tool'
  return (
    current.find((item) => item.kind === 'tool' && item.toolCallId === toolCallId)?.title ?? 'Tool'
  )
}

function reduceToolsEvent(
  current: readonly RunActivity[],
  event: ActivityProtocolEvent,
): RunActivity[] {
  const data = eventData(event)
  if (!isRecord(data)) return reduceActivity(current, toActivityEvent(event, 'tools', data))
  const eventName = textValue(data.event)
  const toolCallId = textValue(data.tool_call_id) ?? textValue(data.id)
  const toolName =
    textValue(data.tool_name) ?? textValue(data.name) ?? knownToolTitle(current, toolCallId)
  const status =
    eventName === 'tool-finished' ? 'completed' : eventName === 'tool-error' ? 'error' : 'running'
  return reduceActivity(
    current,
    toActivityEvent(event, 'tools', {
      ...data,
      tool_name: toolName,
      status,
    }),
  )
}

function reduceStateEvent(
  current: readonly RunActivity[],
  event: ActivityProtocolEvent,
): RunActivity[] {
  const data = eventData(event)
  if (event.method === 'updates' && isRecord(data) && isRecord(data.values)) {
    return reduceActivity(current, toActivityEvent(event, 'updates', data.values))
  }
  return reduceActivity(current, toActivityEvent(event, event.method, data))
}

function reduceLifecycleEvent(
  current: readonly RunActivity[],
  event: ActivityProtocolEvent,
): RunActivity[] {
  const data = eventData(event)
  if (!isRecord(data)) return reduceActivity(current, toActivityEvent(event, 'lifecycle', data))
  const namespace = eventNamespace(event)
  const cause = data.cause
  const causeToolCallId =
    isRecord(cause) && textValue(cause.type) === 'toolCall'
      ? textValue(cause.tool_call_id)
      : undefined
  if (namespace.length === 0 && !causeToolCallId) return [...current]
  return reduceActivity(
    current,
    toActivityEvent(event, 'lifecycle', {
      ...data,
      name: textValue(data.graph_name) ?? textValue(data.name),
      status: data.event,
      trigger_call_id: causeToolCallId ?? data.trigger_call_id,
    }),
  )
}

export function reduceProtocolActivity(
  current: readonly RunActivity[],
  event: ActivityProtocolEvent,
): RunActivity[] {
  if (event.method === 'messages') return reduceMessagesEvent(current, event)
  if (event.method === 'tools') return reduceToolsEvent(current, event)
  if (event.method === 'values' || event.method === 'updates')
    return reduceStateEvent(current, event)
  if (event.method === 'lifecycle') return reduceLifecycleEvent(current, event)
  return reduceActivity(current, toActivityEvent(event, event.method, eventData(event)))
}
