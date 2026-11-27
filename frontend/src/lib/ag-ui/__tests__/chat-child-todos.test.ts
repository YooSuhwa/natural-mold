import { describe, expect, it } from 'vitest'
import { selectAgUiChildTodos } from '../chat-child-todos'
import type { AgUiActivitySnapshot, AgUiMessageLink } from '../chat-history-types'

const child = { id: 'shared-call', subagentRunId: 'child-a', parentMessageId: 'turn-a' }
const first = { id: 'first', content: 'Inspect references', status: 'in_progress' }
const last = { id: 'last', content: 'Prepare report', status: 'completed' }

function activity(todos: unknown, overrides: Partial<AgUiActivitySnapshot> = {}) {
  return {
    type: 'ACTIVITY_SNAPSHOT' as const,
    activityType: 'moldy.subagent_todos',
    messageId: 'plan',
    subagentRunId: child.subagentRunId,
    content: { todos },
    ...overrides,
  }
}

function link(messageId: string, overrides: Partial<AgUiMessageLink> = {}): AgUiMessageLink {
  return {
    messageId,
    physicalRunId: 'attempt',
    subagentRunId: child.subagentRunId,
    parentToolCallId: child.id,
    runId: null,
    uiMessageId: null,
    checkpointId: null,
    parentCheckpointId: null,
    parentMessageId: null,
    ...overrides,
  }
}

describe('child-owned plan activities', () => {
  it('replaces a plan with the latest allowed snapshot, including an empty plan', () => {
    const own = [activity([first]), activity([{ ...last, privateDetail: 'omit' }])]
    const others = [
      activity([first], { subagentRunId: 'child-b' }),
      activity([first], { subagentRunId: undefined }),
      activity([first], { activityType: 'moldy.ui_data' }),
      { ...activity([first]), parentToolCallId: 'different-call' },
    ]
    expect(selectAgUiChildTodos([...own, ...others], child)).toEqual([last])
    expect(selectAgUiChildTodos([...own, activity([]), ...others], child)).toEqual([])
    expect(selectAgUiChildTodos(others, child)).toBeUndefined()
  })

  it('resolves the delegating message wire alias or a message belonging to the child', () => {
    const links = [
      link('parent-wire', {
        subagentRunId: undefined,
        parentToolCallId: undefined,
        uiMessageId: 'turn-a',
      }),
      link('child-text'),
      link('sibling-text', { subagentRunId: 'child-b' }),
    ]
    for (const parentMessageId of ['parent-wire', 'turn-a', 'child-text']) {
      expect(selectAgUiChildTodos([activity([first], { parentMessageId })], child, links)).toEqual([
        first,
      ])
    }
    for (const parentMessageId of ['sibling-text', 'unproven']) {
      expect(
        selectAgUiChildTodos([activity([first], { parentMessageId })], child, links),
      ).toBeUndefined()
    }
  })

  it('rejects contradictory stored owner, parent call and physical attempt bindings', () => {
    const plan = activity([first], { physicalRunId: 'attempt', parentMessageId: 'turn-a' })
    for (const contradicting of [
      link('plan', { subagentRunId: 'child-b' }),
      link('plan', { parentToolCallId: 'other' }),
      link('plan', { parentMessageId: 'turn-b' }),
      link('plan', { physicalRunId: 'old-attempt' }),
      link('turn-a', { subagentRunId: 'child-b', uiMessageId: 'turn-a' }),
    ]) {
      expect(selectAgUiChildTodos([plan], child, [contradicting])).toBeUndefined()
    }
  })

  it('leaves the prior valid plan intact for malformed or ambiguously keyed lists', () => {
    for (const malformed of [
      'private',
      [{ ...first, id: '' }],
      [{ ...first, status: 'arbitrary' }],
      [{ ...first, content: '   ' }],
      [first, first],
      [first, { hidden: true }],
    ]) {
      expect(selectAgUiChildTodos([activity(malformed)], child)).toBeUndefined()
      expect(selectAgUiChildTodos([activity([last]), activity(malformed)], child)).toEqual([last])
    }
  })
})
