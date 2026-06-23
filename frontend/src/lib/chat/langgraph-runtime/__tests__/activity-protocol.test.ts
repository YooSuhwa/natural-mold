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

  it('marks root running activities complete when a terminal lifecycle event is replayed', () => {
    const activities = reduce([
      event(
        'messages',
        {
          event: 'content-block-delta',
          delta: { type: 'text-delta', text: 'hello' },
        },
        { event_id: 'message-1', seq: 1 },
      ),
      event('lifecycle', { event: 'done' }, { event_id: 'done-1', seq: 2 }),
    ])

    expect(activities.find((item) => item.kind === 'responding')).toMatchObject({
      status: 'complete',
    })
    expect(activities.filter((item) => item.status === 'running')).toEqual([])
  })

  it('deduplicates streaming response chunks with different event ids', () => {
    const activities = reduce([
      event(
        'messages',
        {
          event: 'content-block-delta',
          delta: { type: 'text-delta', text: 'hello' },
        },
        { event_id: 'message-1', seq: 1 },
      ),
      event(
        'messages',
        {
          event: 'content-block-delta',
          delta: { type: 'text-delta', text: ' world' },
        },
        { event_id: 'message-2', seq: 2 },
      ),
      event(
        'messages',
        {
          event: 'content-block-delta',
          delta: { type: 'text-delta', text: '!' },
        },
        { event_id: 'message-3', seq: 3 },
      ),
    ])

    expect(activities.filter((item) => item.kind === 'responding')).toHaveLength(1)
    expect(activities.find((item) => item.kind === 'responding')).toMatchObject({
      status: 'running',
      data: { preview: '!' },
    })
  })

  it('deduplicates one tool call across message and tools channels when run ids differ', () => {
    const activities = reduce([
      event(
        'messages',
        {
          event: 'content-block-start',
          index: 0,
          content: {
            type: 'tool_call_chunk',
            id: 'call_e2e_ask_user_fruit',
            name: 'ask_user',
            args: '{"mode":"option_list"}',
          },
        },
        { event_id: 'message-1', run_id: 'model-run-1', seq: 1 },
      ),
      event(
        'tools',
        {
          event: 'tool-started',
          tool_call_id: 'call_e2e_ask_user_fruit',
          tool_name: 'ask_user',
        },
        { event_id: 'synthetic-tool-start-1', seq: 2 },
      ),
      event(
        'tools',
        {
          event: 'tool-finished',
          tool_call_id: 'call_e2e_ask_user_fruit',
          tool_name: 'ask_user',
        },
        { event_id: 'synthetic-tool-finish-1', seq: 3 },
      ),
    ])

    const toolActivities = activities.filter((item) => item.kind === 'tool')
    expect(toolActivities).toHaveLength(1)
    expect(toolActivities[0]).toMatchObject({
      id: 'model-run-1:tool:call_e2e_ask_user_fruit',
      status: 'complete',
      title: 'ask_user',
      toolCallId: 'call_e2e_ask_user_fruit',
    })
  })
})
