import { describe, expect, it } from 'vitest'
import { normalizeSkillBuilderStreamEvent } from '../stream-skill-builder-message'

describe('normalizeSkillBuilderStreamEvent', () => {
  it('keeps builder status payloads typed as objects', () => {
    const event = normalizeSkillBuilderStreamEvent({
      event: 'builder_status',
      data: { status: 'running', phase: 'draft_package' },
    })

    expect(event).toEqual({
      event: 'builder_status',
      data: { status: 'running', phase: 'draft_package' },
    })
  })

  it('supports assistant message wire events from the builder stream', () => {
    const event = normalizeSkillBuilderStreamEvent({
      event: 'content_delta',
      data: { delta: '검토 준비 완료' },
      id: 'evt-1',
    })

    expect(event).toEqual({
      event: 'content_delta',
      data: { delta: '검토 준비 완료' },
      id: 'evt-1',
    })
  })

  it('drops malformed non-object payloads', () => {
    const event = normalizeSkillBuilderStreamEvent({
      event: 'eval_result',
      data: 'not-an-object',
    })

    expect(event).toEqual({ event: 'eval_result', data: {} })
  })
})
