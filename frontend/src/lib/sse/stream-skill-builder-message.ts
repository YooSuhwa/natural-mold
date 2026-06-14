import { streamSSEPost } from './parse-sse'
import type { SSEEvent } from './parse-sse'
import type { JsonValue } from '@/lib/types/json'
import type {
  SkillBuilderMessageRequest,
  SkillBuilderStreamEvent,
  SkillBuilderStreamEventType,
} from '@/lib/types/skill-builder'

export async function* streamSkillBuilderMessage(
  sessionId: string,
  data: SkillBuilderMessageRequest,
  signal?: AbortSignal,
): AsyncGenerator<SkillBuilderStreamEvent> {
  for await (const event of streamSSEPost<SkillBuilderStreamEventType>(
    `/api/skill-builder/${sessionId}/messages`,
    data,
    signal,
    'builder_status',
  )) {
    yield normalizeSkillBuilderStreamEvent(event)
  }
}

export async function* streamSkillBuilderResume(
  sessionId: string,
  data: SkillBuilderMessageRequest,
  signal?: AbortSignal,
): AsyncGenerator<SkillBuilderStreamEvent> {
  for await (const event of streamSSEPost<SkillBuilderStreamEventType>(
    `/api/skill-builder/${sessionId}/messages/resume`,
    data,
    signal,
    'builder_status',
  )) {
    yield normalizeSkillBuilderStreamEvent(event)
  }
}

export function normalizeSkillBuilderStreamEvent(
  event: SSEEvent<SkillBuilderStreamEventType>,
): SkillBuilderStreamEvent {
  const data = jsonObject(event.data)
  switch (event.event) {
    case 'message_start':
      return { event: event.event, data, id: event.id }
    case 'builder_status':
      return { event: event.event, data, id: event.id }
    case 'builder_activity':
      return { event: event.event, data, id: event.id }
    case 'draft_package':
      return { event: event.event, data, id: event.id }
    case 'validation_result':
      return { event: event.event, data, id: event.id }
    case 'compatibility_result':
      return { event: event.event, data, id: event.id }
    case 'changelog_draft':
      return { event: event.event, data, id: event.id }
    case 'eval_result':
      return { event: event.event, data, id: event.id }
    case 'content_delta':
      return { event: event.event, data, id: event.id }
    case 'message_end':
      return { event: event.event, data, id: event.id }
    case 'error':
      return { event: event.event, data, id: event.id }
    default:
      return assertNever(event.event)
  }
}

function jsonObject(value: unknown): Readonly<Record<string, JsonValue>> {
  if (!isJsonObject(value)) {
    return {}
  }
  return value
}

function isJsonObject(value: unknown): value is Readonly<Record<string, JsonValue>> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false
  }
  return Object.values(value).every(isJsonValue)
}

function isJsonValue(value: unknown): value is JsonValue {
  if (
    value === null ||
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return true
  }
  if (Array.isArray(value)) {
    return value.every(isJsonValue)
  }
  return isJsonObject(value)
}

function assertNever(value: never): never {
  throw new Error(`Unexpected Skill Builder stream event: ${value}`)
}
