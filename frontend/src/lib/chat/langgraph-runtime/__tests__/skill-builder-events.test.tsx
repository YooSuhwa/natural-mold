import { renderHook } from '@testing-library/react'
import { Provider, createStore } from 'jotai'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import {
  protocolSkillDraftBrief,
  protocolSkillValidation,
  useLangGraphSkillBuilderEffects,
} from '../skill-builder-events'
import { chatSkillDraftBriefAtom, chatSkillValidationAtom } from '@/lib/stores/chat-skill-builder'

const mocks = vi.hoisted(() => ({
  useChannelEffect: vi.fn(),
}))

vi.mock('@langchain/react', () => ({
  useChannelEffect: mocks.useChannelEffect,
}))

function draftEvent(overrides: Record<string, unknown> = {}) {
  return {
    method: 'custom',
    event_id: 'run-1:skill_draft',
    seq: 1,
    params: {
      data: {
        name: 'moldy.skill_draft',
        payload: {
          session_id: 's1',
          mode: 'create',
          slug: 'notes',
          file_count: 2,
          files: [
            { path: 'SKILL.md', size: 120 },
            { path: 'references/guide.md', size: 40 },
          ],
          changed_count: 2,
          ...overrides,
        },
      },
    },
  }
}

function validationEvent() {
  return {
    method: 'custom',
    event_id: 'evt-1:skill_validation',
    seq: 5,
    params: {
      data: {
        name: 'moldy.skill_validation',
        payload: {
          tool_name: 'validate_skill',
          session_id: 's1',
          validation_result: { valid: false, error_count: 1, issues: [] },
        },
      },
    },
  }
}

describe('protocolSkillDraftBrief', () => {
  it('moldy.skill_draft 이벤트에서 드래프트 요약을 파싱한다', () => {
    const brief = protocolSkillDraftBrief(draftEvent())
    expect(brief).toEqual({
      session_id: 's1',
      mode: 'create',
      slug: 'notes',
      file_count: 2,
      files: [
        { path: 'SKILL.md', size: 120 },
        { path: 'references/guide.md', size: 40 },
      ],
      changed_count: 2,
    })
  })

  it('다른 custom 이벤트는 무시한다', () => {
    const event = draftEvent()
    ;(event.params.data as { name: string }).name = 'moldy.memory_recalled'
    expect(protocolSkillDraftBrief(event)).toBeNull()
  })

  it('session_id 없는 페이로드는 무시한다', () => {
    expect(protocolSkillDraftBrief(draftEvent({ session_id: undefined }))).toBeNull()
  })
})

describe('protocolSkillValidation', () => {
  it('moldy.skill_validation 이벤트에서 검증 결과를 파싱한다', () => {
    const snapshot = protocolSkillValidation(validationEvent())
    expect(snapshot).toEqual({
      tool_name: 'validate_skill',
      session_id: 's1',
      validation_result: { valid: false, error_count: 1, issues: [] },
    })
  })

  it('validation_result 없는 페이로드는 무시한다', () => {
    const event = validationEvent()
    delete (event.params.data.payload as Record<string, unknown>).validation_result
    expect(protocolSkillValidation(event)).toBeNull()
  })
})

describe('useLangGraphSkillBuilderEffects', () => {
  function setup(conversationId = 'conv-1') {
    const store = createStore()
    const wrapper = ({ children }: { children: ReactNode }) => (
      <Provider store={store}>{children}</Provider>
    )
    mocks.useChannelEffect.mockClear()
    renderHook(
      () =>
        useLangGraphSkillBuilderEffects({
          stream: {} as never,
          conversationId,
        }),
      { wrapper },
    )
    const call = mocks.useChannelEffect.mock.calls[0]
    expect(call[1]).toEqual(['custom'])
    expect(call[2].replay).toBe(true)
    const onEvent = call[2].onEvent as (event: unknown) => void
    return { store, onEvent }
  }

  it('드래프트/검증 이벤트를 대화 스코프 스토어에 반영한다', () => {
    const { store, onEvent } = setup()

    onEvent(draftEvent())
    onEvent(validationEvent())

    expect(store.get(chatSkillDraftBriefAtom)['conv-1']?.slug).toBe('notes')
    expect(store.get(chatSkillValidationAtom)['conv-1']?.validation_result).toEqual({
      valid: false,
      error_count: 1,
      issues: [],
    })
  })

  it('같은 event_id의 replay 재전달은 dedup한다', () => {
    const { store, onEvent } = setup()

    onEvent(draftEvent())
    const first = store.get(chatSkillDraftBriefAtom)['conv-1']
    onEvent(draftEvent({ slug: 'changed-should-be-ignored' }))

    expect(store.get(chatSkillDraftBriefAtom)['conv-1']).toBe(first)
  })

  it('새 run의 이벤트(새 event_id)는 최신값으로 교체한다', () => {
    const { store, onEvent } = setup()

    onEvent(draftEvent())
    const secondRun = draftEvent({ slug: 'notes-v2' })
    secondRun.event_id = 'run-2:skill_draft'
    onEvent(secondRun)

    expect(store.get(chatSkillDraftBriefAtom)['conv-1']?.slug).toBe('notes-v2')
  })
})
