import { describe, expect, it } from 'vitest'
import { createStore } from 'jotai'
import {
  chatSubagentNamesAtom,
  mergeConversationSubagentNamesAtom,
  resolveSubagentDisplayName,
} from '../chat-subagent-names'

describe('chat-subagent-names store', () => {
  it('같은 conversation의 매핑을 누적 병합한다', () => {
    const store = createStore()
    store.set(mergeConversationSubagentNamesAtom, {
      conversationId: 'c1',
      names: { agent_11111111: '리서처' },
    })
    store.set(mergeConversationSubagentNamesAtom, {
      conversationId: 'c1',
      names: { agent_22222222: '작성자' },
    })

    expect(store.get(chatSubagentNamesAtom)).toEqual({
      c1: { agent_11111111: '리서처', agent_22222222: '작성자' },
    })
  })

  it('다른 conversation은 서로 격리한다', () => {
    const store = createStore()
    store.set(mergeConversationSubagentNamesAtom, { conversationId: 'c1', names: { a: 'A' } })
    store.set(mergeConversationSubagentNamesAtom, { conversationId: 'c2', names: { b: 'B' } })

    expect(store.get(chatSubagentNamesAtom)).toEqual({ c1: { a: 'A' }, c2: { b: 'B' } })
  })

  it('같은 매핑을 다시 병합해도 idempotent하다 (replay 안전)', () => {
    const store = createStore()
    const payload = { conversationId: 'c1', names: { a: 'A' } }
    store.set(mergeConversationSubagentNamesAtom, payload)
    store.set(mergeConversationSubagentNamesAtom, payload)

    expect(store.get(chatSubagentNamesAtom)).toEqual({ c1: { a: 'A' } })
  })

  it('resolveSubagentDisplayName은 매핑이 있으면 치환, 없으면 raw name을 반환한다', () => {
    expect(resolveSubagentDisplayName(undefined, 'agent_123')).toBe('agent_123')
    expect(resolveSubagentDisplayName({ agent_123: '봇' }, 'agent_123')).toBe('봇')
    expect(resolveSubagentDisplayName({ other: 'x' }, 'agent_123')).toBe('agent_123')
  })
})
