import { describe, expect, it } from 'vitest'
import { reduceProtocolActivity, type ActivityProtocolEvent } from '../activity-protocol'
import type { RunActivity } from '../activity-model'

function event(
  method: ActivityProtocolEvent['method'],
  data: unknown,
  overrides: Partial<ActivityProtocolEvent> = {},
): ActivityProtocolEvent {
  return {
    type: 'event',
    event_id: 'event-1',
    method,
    seq: 1,
    params: {
      namespace: [],
      timestamp: 1_765_600_000_000,
      data,
    },
    ...overrides,
  }
}

function reduce(events: readonly ActivityProtocolEvent[]): RunActivity[] {
  return events.reduce<RunActivity[]>((current, item) => reduceProtocolActivity(current, item), [])
}

describe('reduceProtocolActivity', () => {
  it('maps v3 content-block tool calls through tool completion', () => {
    const activities = reduce([
      event('messages', {
        event: 'content-block-start',
        index: 0,
        content: {
          type: 'tool_call_chunk',
          id: 'tc-1',
          name: 'web_search',
          args: '{"q":"moldy"}',
        },
      }),
      event('tools', {
        event: 'tool-finished',
        tool_call_id: 'tc-1',
        output: 'done',
      }),
    ])

    expect(activities.find((item) => item.kind === 'tool')).toMatchObject({
      id: 'event-1:tool:tc-1',
      status: 'complete',
      title: 'web_search',
      toolCallId: 'tc-1',
    })
  })

  it('unwraps v3 updates.values state before reducing todos', () => {
    const activities = reduce([
      event('updates', {
        node: 'agent',
        values: {
          todos: [{ id: 'todo-1', content: 'Plan', status: 'in_progress' }],
        },
      }),
    ])

    expect(activities).toEqual([
      expect.objectContaining({
        kind: 'planning',
        status: 'running',
        title: 'Planning',
      }),
    ])
  })
})
