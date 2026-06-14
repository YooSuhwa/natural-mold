import { describe, expect, it } from 'vitest'

import { extractChips } from './extract-chips'
import type { ProtocolTraceEvent, TurnTrace } from '@/lib/types/share'

function turn(events: TurnTrace['events']): TurnTrace {
  return {
    assistant_msg_id: 'm1',
    events,
    last_event_id: events[events.length - 1]?.id ?? null,
    linked_message_ids: null,
    created_at: '2026-05-03T00:00:00Z',
    completed_at: '2026-05-03T00:00:01Z',
  }
}

function protocolEvent(
  method: string,
  data: unknown,
  overrides: Partial<ProtocolTraceEvent> = {},
): ProtocolTraceEvent {
  return {
    id: overrides.id ?? 'protocol-event',
    method,
    params: { namespace: [], data, ...overrides.params },
    seq: overrides.seq ?? null,
    event_id: overrides.event_id ?? null,
    upstream_event_id: overrides.upstream_event_id ?? null,
    run_id: overrides.run_id ?? null,
    type: overrides.type ?? null,
  }
}

describe('extractChips canonical protocol events', () => {
  it('renders a tool chip from message tool chunks and tool completion', () => {
    const events = [
      protocolEvent('messages', {
        tool_call_chunks: [{ id: 'tc-1', name: 'web_search', args: { q: 'moldy' } }],
      }),
      protocolEvent('tools', { event: 'tool-finished', tool_call_id: 'tc-1', output: 'done!' }),
    ]

    expect(extractChips(turn(events))).toEqual([
      { kind: 'tool', status: 'success', title: 'web_search', meta: '5 chars' },
    ])
  })

  it('renders a subagent chip from lifecycle namespace events', () => {
    const events = [
      protocolEvent(
        'lifecycle',
        { status: 'running', name: 'researcher' },
        { params: { namespace: ['supervisor', 'researcher'] } },
      ),
    ]

    expect(extractChips(turn(events))).toEqual([
      { kind: 'subagent', status: 'success', title: 'researcher' },
    ])
  })

  it('renders a subagent chip from subgraphs alias events', () => {
    const events = [
      protocolEvent(
        'subgraphs',
        { status: 'completed', graph_name: 'analyst' },
        { params: { namespace: ['supervisor', 'analyst'] }, event_id: 'evt-subgraphs-1' },
      ),
    ]

    expect(extractChips(turn(events))).toEqual([
      { kind: 'subagent', status: 'success', title: 'analyst' },
    ])
  })

  it('deduplicates the same subagent discovered through task tool and lifecycle events', () => {
    const events = [
      protocolEvent('messages', {
        tool_call_chunks: [
          {
            id: 'task-1',
            name: 'task',
            args: { subagent_type: 'researcher', description: 'find docs' },
          },
        ],
      }),
      protocolEvent(
        'lifecycle',
        { status: 'running', name: 'researcher' },
        { params: { namespace: ['tools:task-1'] } },
      ),
    ]

    expect(extractChips(turn(events))).toEqual([
      { kind: 'subagent', status: 'success', title: 'researcher' },
    ])
  })

  it('renders an artifact chip from custom file events', () => {
    const events = [
      protocolEvent('custom:file_event', {
        op: 'created',
        display_name: 'report.md',
        path: 'report.md',
      }),
    ]

    expect(extractChips(turn(events))).toEqual([
      { kind: 'tool', status: 'success', title: 'report.md', meta: 'file_event' },
    ])
  })

  it('renders a memory chip from custom memory events', () => {
    const events = [
      protocolEvent('custom:memory_saved', {
        scope: 'user',
        content: 'User prefers concise answers.',
      }),
    ]

    expect(extractChips(turn(events))).toEqual([
      { kind: 'tool', status: 'success', title: 'memory_saved' },
    ])
  })
})
