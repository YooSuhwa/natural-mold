import { describe, expect, it } from 'vitest'
import { AIMessage, HumanMessage } from '@langchain/core/messages'
import {
  attachCompactionToMessages,
  compactionFromMessage,
  compactionMarkerFromPayload,
  computeCompactionByMessageId,
  type CompactionMarker,
} from '../compaction-events'
import { reduceActivity } from '../activity-model'
import type { ProtocolEvent } from '../activity-types'

function compactionEvent(seq: number, payload: Record<string, unknown>): ProtocolEvent {
  return {
    method: 'custom',
    seq,
    run_id: 'run-1',
    params: { data: { name: 'moldy.compaction', payload } },
  }
}

function messageStartEvent(seq: number, id: string): ProtocolEvent {
  return { method: 'messages', seq, params: { data: { event: 'message-start', id } } }
}

describe('computeCompactionByMessageId', () => {
  it('done 마커를 직전 마지막 message-start에 매핑한다', () => {
    // 실측 순서: running(2) → 답변 message-start(7) → done(12)
    const events = [
      compactionEvent(2, { state: 'running' }),
      messageStartEvent(7, 'answer-msg'),
      compactionEvent(12, {
        state: 'done',
        history_id: 'hist_opaque',
        cutoff_index: 2,
        offload_path: '/private/runtime/conversation_history/legacy.md',
      }),
    ]

    const map = computeCompactionByMessageId(events)

    expect(map.get('answer-msg')).toEqual({
      historyId: 'hist_opaque',
      cutoffIndex: 2,
    })
  })

  it('running 마커만 있고 done이 없으면 비어 있다', () => {
    const events = [compactionEvent(2, { state: 'running' }), messageStartEvent(3, 'm')]

    expect(computeCompactionByMessageId(events).size).toBe(0)
  })

  it('done보다 늦은 message-start는 매핑하지 않는다', () => {
    const events = [
      messageStartEvent(7, 'earlier'),
      compactionEvent(12, { state: 'done', history_id: 'hist_earlier' }),
      messageStartEvent(20, 'later'),
    ]

    const map = computeCompactionByMessageId(events)

    expect(map.get('earlier')).toEqual({ historyId: 'hist_earlier' })
    expect(map.has('later')).toBe(false)
  })

  it('반복된 완료 이벤트를 각 답변에 독립적으로 매핑한다', () => {
    const events = [
      messageStartEvent(7, 'first-answer'),
      compactionEvent(12, { state: 'done', history_id: 'history-first', cutoff_index: 2 }),
      messageStartEvent(20, 'second-answer'),
      compactionEvent(26, { state: 'done', history_id: 'history-second', cutoff_index: 4 }),
    ]

    expect(computeCompactionByMessageId(events)).toEqual(
      new Map<string, CompactionMarker>([
        ['first-answer', { historyId: 'history-first', cutoffIndex: 2 }],
        ['second-answer', { historyId: 'history-second', cutoffIndex: 4 }],
      ]),
    )
  })

  it('중첩된 message payload에서도 반복 완료 이벤트를 각 답변에 매핑한다', () => {
    const events = [
      {
        method: 'messages',
        seq: 7,
        params: {
          data: [{ event: 'message-start', id: 'nested-first' }, { langgraph_node: 'model' }],
        },
      },
      compactionEvent(12, { state: 'done', history_id: 'history-first' }),
      {
        method: 'messages',
        seq: 20,
        params: {
          data: [{ event: 'message-start', id: 'nested-second' }, { langgraph_node: 'model' }],
        },
      },
      compactionEvent(26, { state: 'done', history_id: 'history-second' }),
    ] satisfies readonly ProtocolEvent[]

    expect(computeCompactionByMessageId(events)).toEqual(
      new Map<string, CompactionMarker>([
        ['nested-first', { historyId: 'history-first' }],
        ['nested-second', { historyId: 'history-second' }],
      ]),
    )
  })

  it('사용자에게 투영하지 않는 upstream 필드는 marker에서 제거한다', () => {
    const marker = compactionMarkerFromPayload({
      state: 'done',
      history_id: 'history-opaque',
      cutoff_index: 3,
      offload_path: '/private/runtime/conversation_history/summary.md',
      summary: 'raw upstream summary must not reach the chat',
      token_count: 987,
    })

    expect(marker).toEqual({ historyId: 'history-opaque', cutoffIndex: 3 })
    expect(JSON.stringify(marker)).not.toContain('private')
    expect(JSON.stringify(marker)).not.toContain('raw upstream summary')
  })
})

describe('attachCompactionToMessages + compactionFromMessage', () => {
  it('매핑된 메시지에 마커를 붙이고 다시 읽는다', () => {
    const messages = [
      new HumanMessage({ id: 'u', content: 'q' }),
      new AIMessage({ id: 'answer-msg', content: 'a' }),
    ]
    const map = new Map<string, CompactionMarker>([
      ['answer-msg', { historyId: 'hist_opaque', cutoffIndex: 1 }],
    ])

    const attached = attachCompactionToMessages(messages, map)

    expect(compactionFromMessage(attached[1])).toEqual({ historyId: 'hist_opaque', cutoffIndex: 1 })
    expect(compactionFromMessage(attached[0])).toBeNull()
  })

  it('렌더되지 않은 id는 마지막 assistant 메시지로 폴백한다', () => {
    const messages = [new AIMessage({ id: 'visible-answer', content: 'a' })]
    const map = new Map<string, CompactionMarker>([['stale-id', { historyId: 'hist_stale' }]])

    const attached = attachCompactionToMessages(messages, map)

    expect(compactionFromMessage(attached[0])).toEqual({ historyId: 'hist_stale' })
  })

  it('마커가 없으면 같은 배열 참조를 반환한다', () => {
    const messages = [new AIMessage({ id: 'a', content: 'a' })]

    expect(attachCompactionToMessages(messages, new Map())).toBe(messages)
  })
})

describe('reduceActivity — compaction', () => {
  it('running 동안 압축 activity를 띄우고 done에서 같은 pill을 complete로 전이한다', () => {
    const running = reduceActivity([], compactionEvent(2, { state: 'running' }))
    expect(running).toHaveLength(1)
    expect(running[0].kind).toBe('compaction')
    expect(running[0].status).toBe('running')

    const done = reduceActivity(
      running,
      compactionEvent(12, { state: 'done', history_id: 'hist_complete' }),
    )
    // same activity id (run:compaction:compaction) → upsert, not a second pill.
    expect(done).toHaveLength(1)
    expect(done[0].status).toBe('complete')
  })
})
