import { streamSSEPost } from './parse-sse'
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
  yield* streamSSEPost<SkillBuilderStreamEventType>(
    `/api/skill-builder/${sessionId}/messages`,
    data,
    signal,
    'builder_status',
  )
}

export async function* streamSkillBuilderResume(
  sessionId: string,
  data: SkillBuilderMessageRequest,
  signal?: AbortSignal,
): AsyncGenerator<SkillBuilderStreamEvent> {
  yield* streamSSEPost<SkillBuilderStreamEventType>(
    `/api/skill-builder/${sessionId}/messages/resume`,
    data,
    signal,
    'builder_status',
  )
}
