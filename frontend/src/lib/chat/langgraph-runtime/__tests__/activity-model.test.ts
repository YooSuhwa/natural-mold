import { describe, expect, it } from 'vitest'
import { reduceActivity, type ProtocolEvent, type RunActivity } from '../activity-model'

function event(method: string, data: unknown, extra: Partial<ProtocolEvent> = {}): ProtocolEvent {
  return {
    method,
    run_id: 'run-1',
    seq: 1,
    params: { namespace: [], data },
    ...extra,
  }
}

function reduce(events: ProtocolEvent[]): RunActivity[] {
  return events.reduce<RunActivity[]>((current, item) => reduceActivity(current, item), [])
}

describe('reduceActivity', () => {
  it('tracks one tool call from message chunks through tool completion', () => {
    const activities = reduce([
      event('messages', {
        tool_call_chunks: [{ id: 'tc-1', name: 'search', args: '{"q":"moldy"}' }],
      }),
      event('tools', {
        tool_call_id: 'tc-1',
        tool_name: 'search',
        status: 'completed',
        output: 'done',
      }),
    ])

    const tool = activities.find((item) => item.kind === 'tool')
    expect(tool).toMatchObject({
      id: 'run-1:tool:tc-1',
      status: 'complete',
      title: 'search',
      toolCallId: 'tc-1',
    })
  })

  it('creates a nested subagent activity from a namespace', () => {
    const activities = reduce([
      event(
        'messages',
        { chunk: 'draft' },
        { params: { namespace: ['tools:tc-1'], data: { chunk: 'draft' } } },
      ),
    ])

    expect(activities).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: 'run-1:subagent:tools:tc-1',
          kind: 'subagent',
          status: 'running',
          title: 'tools:tc-1',
        }),
      ]),
    )
  })

  it('maps lifecycle task events to subagent status', () => {
    const activities = reduce([
      event('lifecycle', {
        trigger_call_id: 'tc-2',
        name: 'Writer',
        status: 'started',
      }),
      event('tasks', {
        trigger_call_id: 'tc-2',
        name: 'Writer',
        status: 'completed',
      }),
    ])

    expect(activities.find((item) => item.id === 'run-1:subagent:tc-2')).toMatchObject({
      kind: 'subagent',
      status: 'complete',
      title: 'Writer',
      toolCallId: 'tc-2',
    })
  })

  it('maps async_tasks state to background subagent activities', () => {
    const activities = reduce([
      event('values', {
        async_tasks: [{ id: 'bg-1', name: 'Background writer', status: 'success' }],
      }),
    ])

    expect(activities).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: 'run-1:background_subagent:bg-1',
          kind: 'background_subagent',
          status: 'complete',
          title: 'Background writer',
        }),
      ]),
    )
  })

  it('maps todos to planning and custom extension events to domain activities', () => {
    const activities = reduce([
      event('updates', {
        todos: [{ id: 'todo-1', content: 'Plan', status: 'in_progress' }],
      }),
      event('custom', { name: 'memory_saved', payload: { key: 'profile' } }),
      event('custom', { name: 'file_event', payload: { path: 'report.md' } }),
    ])

    expect(activities.map((item) => item.kind)).toEqual(['planning', 'memory', 'artifact'])
    expect(activities[0]).toMatchObject({ status: 'running', title: 'Planning' })
  })

  it('marks active activities as error when an error event arrives', () => {
    const activities = reduce([
      event('messages', { chunk: 'hello' }),
      event('error', { message: 'boom' }),
    ])

    expect(activities.find((item) => item.kind === 'responding')).toMatchObject({
      status: 'error',
    })
    expect(activities.find((item) => item.kind === 'error')).toMatchObject({
      title: 'Error',
      status: 'error',
    })
  })
})
