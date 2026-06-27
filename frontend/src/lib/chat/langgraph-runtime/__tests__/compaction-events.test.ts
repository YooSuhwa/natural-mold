import { describe, expect, it } from 'vitest'
import { AIMessage, HumanMessage } from '@langchain/core/messages'
import {
  attachCompactionToMessages,
  compactionFromMessage,
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
        offload_path: '/conversation_history/t.md',
        cutoff_index: 2,
      }),
    ]

    const map = computeCompactionByMessageId(events)

    expect(map.get('answer-msg')).toEqual({
      offloadPath: '/conversation_history/t.md',
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
      compactionEvent(12, { state: 'done', offload_path: '/a.md' }),
      messageStartEvent(20, 'later'),
    ]

    const map = computeCompactionByMessageId(events)

    expect(map.get('earlier')).toEqual({ offloadPath: '/a.md' })
    expect(map.has('later')).toBe(false)
  })
})

describe('attachCompactionToMessages + compactionFromMessage', () => {
  it('매핑된 메시지에 마커를 붙이고 다시 읽는다', () => {
    const messages = [
      new HumanMessage({ id: 'u', content: 'q' }),
      new AIMessage({ id: 'answer-msg', content: 'a' }),
    ]
    const map = new Map<string, CompactionMarker>([
      ['answer-msg', { offloadPath: '/x.md', cutoffIndex: 1 }],
    ])

    const attached = attachCompactionToMessages(messages, map)

    expect(compactionFromMessage(attached[1])).toEqual({ offloadPath: '/x.md', cutoffIndex: 1 })
    expect(compactionFromMessage(attached[0])).toBeNull()
  })

  it('렌더되지 않은 id는 마지막 assistant 메시지로 폴백한다', () => {
    const messages = [new AIMessage({ id: 'visible-answer', content: 'a' })]
    const map = new Map<string, CompactionMarker>([['stale-id', { offloadPath: '/y.md' }]])

    const attached = attachCompactionToMessages(messages, map)

    expect(compactionFromMessage(attached[0])).toEqual({ offloadPath: '/y.md' })
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
      compactionEvent(12, { state: 'done', offload_path: '/t.md' }),
    )
    // same activity id (run:compaction:compaction) → upsert, not a second pill.
    expect(done).toHaveLength(1)
    expect(done[0].status).toBe('complete')
  })
})
