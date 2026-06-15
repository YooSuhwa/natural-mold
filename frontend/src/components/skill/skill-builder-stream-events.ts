import type { SkillBuilderStreamEvent } from '@/lib/types'

export function handleSkillBuilderStreamEvent(event: SkillBuilderStreamEvent): void {
  switch (event.event) {
    case 'message_start':
    case 'builder_status':
    case 'builder_activity':
    case 'draft_package':
    case 'validation_result':
    case 'compatibility_result':
    case 'changelog_draft':
    case 'eval_result':
    case 'content_delta':
    case 'message_end':
      return
    case 'error':
      throw new SkillBuilderStreamEventError(streamMessage(event.data.message))
    default:
      assertNever(event)
  }
}

export class SkillBuilderStreamEventError extends Error {
  constructor(readonly streamMessage: string | null) {
    super('skill_builder_stream_error')
  }
}

function streamMessage(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

function assertNever(value: never): never {
  throw new Error(`Unexpected Skill Builder event: ${JSON.stringify(value)}`)
}
