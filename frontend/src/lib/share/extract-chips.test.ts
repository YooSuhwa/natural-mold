import { describe, expect, it } from 'vitest'

import { extractChips, findTurnForMessage } from './extract-chips'
import type { TurnTrace } from '@/lib/types/share'

function _turn(events: TurnTrace['events'], msgId = 'm1'): TurnTrace {
  return {
    assistant_msg_id: msgId,
    events,
    last_event_id: events[events.length - 1]?.id ?? null,
    linked_message_ids: null,
    created_at: '2026-05-03T00:00:00Z',
    completed_at: '2026-05-03T00:00:01Z',
  }
}

describe('extractChips', () => {
  it('content 전용 turn은 빈 배열을 반환한다', () => {
    const turn = _turn([
      { id: 'm1-1', event: 'message_start', data: { id: 'm1' } },
      { id: 'm1-2', event: 'content_delta', data: { delta: '안녕' } },
      { id: 'm1-3', event: 'message_end', data: { content: '안녕', usage: {} } },
    ])
    expect(extractChips(turn)).toEqual([])
  })

  it('tool_call_start + tool_call_result를 1:1로 매칭한다', () => {
    const turn = _turn([
      { id: 'm1-1', event: 'message_start', data: { id: 'm1' } },
      {
        id: 'm1-2',
        event: 'tool_call_start',
        data: { tool_name: 'web_search', parameters: { q: 'moldy' } },
      },
      {
        id: 'm1-3',
        event: 'tool_call_result',
        data: { tool_name: 'web_search', result: 'AAAAA' },
      },
      { id: 'm1-4', event: 'message_end', data: { content: '...', usage: {} } },
    ])
    const chips = extractChips(turn)
    expect(chips).toHaveLength(1)
    expect(chips[0]).toMatchObject({
      kind: 'tool',
      status: 'success',
      title: 'web_search',
      meta: '5 chars',
    })
  })

  it('"task" 도구는 subagent kind 칩으로 변환된다', () => {
    const turn = _turn([
      {
        id: 'm1-1',
        event: 'tool_call_start',
        data: {
          tool_name: 'task',
          parameters: { agent_name: 'researcher', input: 'X에 대해 조사' },
        },
      },
    ])
    const chips = extractChips(turn)
    expect(chips).toEqual([
      {
        kind: 'subagent',
        status: 'success',
        title: 'researcher',
      },
    ])
  })

  it('subagent 이름이 없으면 subagent_type으로 폴백', () => {
    const turn = _turn([
      {
        id: 'm1-1',
        event: 'tool_call_start',
        data: { tool_name: 'task', parameters: { subagent_type: 'planner' } },
      },
    ])
    expect(extractChips(turn)[0].title).toBe('planner')
  })

  it('subagent 이름/타입 모두 없으면 "Sub-agent"', () => {
    const turn = _turn([
      {
        id: 'm1-1',
        event: 'tool_call_start',
        data: { tool_name: 'task', parameters: {} },
      },
    ])
    expect(extractChips(turn)[0].title).toBe('Sub-agent')
  })

  it('"write_todos"는 Plan 칩으로 변환되고 진행률을 meta에 단다', () => {
    const turn = _turn([
      {
        id: 'm1-1',
        event: 'tool_call_start',
        data: {
          tool_name: 'write_todos',
          parameters: {
            todos: [{ status: 'completed' }, { status: 'completed' }, { status: 'pending' }],
          },
        },
      },
    ])
    expect(extractChips(turn)).toEqual([
      {
        kind: 'tool',
        status: 'success',
        title: 'Plan',
        meta: '2/3',
      },
    ])
  })

  it('write_todos가 todos 비어있으면 meta 없음', () => {
    const turn = _turn([
      {
        id: 'm1-1',
        event: 'tool_call_start',
        data: { tool_name: 'write_todos', parameters: {} },
      },
    ])
    expect(extractChips(turn)[0]).toEqual({
      kind: 'tool',
      status: 'success',
      title: 'Plan',
      meta: undefined,
    })
  })

  it('같은 도구가 여러 번 호출되면 결과를 FIFO로 매칭한다', () => {
    const turn = _turn([
      {
        id: 'm1-1',
        event: 'tool_call_start',
        data: { tool_name: 'web_search', parameters: { q: 'A' } },
      },
      {
        id: 'm1-2',
        event: 'tool_call_start',
        data: { tool_name: 'web_search', parameters: { q: 'B' } },
      },
      {
        id: 'm1-3',
        event: 'tool_call_result',
        data: { tool_name: 'web_search', result: 'first' },
      },
      {
        id: 'm1-4',
        event: 'tool_call_result',
        data: { tool_name: 'web_search', result: 'second-longer' },
      },
    ])
    const chips = extractChips(turn)
    expect(chips).toHaveLength(2)
    expect(chips[0].meta).toBe('5 chars')
    expect(chips[1].meta).toBe('13 chars')
  })

  it('tool_call_start만 있고 result 없어도 success로 표시 (스트림 종료 후 표시용)', () => {
    const turn = _turn([
      {
        id: 'm1-1',
        event: 'tool_call_start',
        data: { tool_name: 'orphan', parameters: {} },
      },
    ])
    const chips = extractChips(turn)
    expect(chips).toHaveLength(1)
    expect(chips[0].status).toBe('success')
    expect(chips[0].meta).toBeUndefined()
  })

  it('도구명이 비어있는 tool_call_start는 무시한다', () => {
    const turn = _turn([
      { id: 'm1-1', event: 'tool_call_start', data: { tool_name: '', parameters: {} } },
      {
        id: 'm1-2',
        event: 'tool_call_start',
        data: { tool_name: 'real', parameters: {} },
      },
    ])
    const chips = extractChips(turn)
    expect(chips).toHaveLength(1)
    expect(chips[0].title).toBe('real')
  })
})

describe('findTurnForMessage', () => {
  it('assistant_msg_id로 매칭되는 turn을 반환', () => {
    const t1 = _turn([], 'msg-A')
    const t2 = _turn([], 'msg-B')
    expect(findTurnForMessage([t1, t2], 'msg-B')).toBe(t2)
  })

  it('매칭 없으면 null', () => {
    const t1 = _turn([], 'msg-A')
    expect(findTurnForMessage([t1], 'unknown')).toBeNull()
  })

  it('빈 배열이면 null', () => {
    expect(findTurnForMessage([], 'msg-A')).toBeNull()
  })
})
