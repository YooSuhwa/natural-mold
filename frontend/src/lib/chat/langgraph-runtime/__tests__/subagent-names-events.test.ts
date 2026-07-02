import { describe, expect, it } from 'vitest'
import { protocolSubagentNames } from '../subagent-names-events'

describe('protocolSubagentNames', () => {
  it('moldy.subagent_names custom 이벤트에서 매핑을 파싱한다', () => {
    const event = {
      method: 'custom',
      event_id: 'run-1:subagent_names',
      seq: 1,
      params: {
        data: {
          name: 'moldy.subagent_names',
          payload: { names: { agent_11111111: '리서처', agent_22222222: '작성자' } },
        },
      },
    }

    expect(protocolSubagentNames(event)).toEqual({
      agent_11111111: '리서처',
      agent_22222222: '작성자',
    })
  })

  it('custom: prefixed method 형식도 인식한다', () => {
    const event = {
      method: 'custom:moldy.subagent_names',
      params: { data: { payload: { names: { a: 'A' } } } },
    }

    expect(protocolSubagentNames(event)).toEqual({ a: 'A' })
  })

  it('다른 custom 이벤트(compaction 등)는 무시한다', () => {
    const event = {
      method: 'custom',
      params: { data: { name: 'moldy.compaction', payload: { state: 'running' } } },
    }

    expect(protocolSubagentNames(event)).toBeNull()
  })

  it('빈 문자열/비문자열 display name은 제외한다', () => {
    const event = {
      method: 'custom',
      params: {
        data: { name: 'moldy.subagent_names', payload: { names: { a: '  ', b: 42, c: '작성자' } } },
      },
    }

    expect(protocolSubagentNames(event)).toEqual({ c: '작성자' })
  })

  it('names가 비었으면 null을 반환한다', () => {
    const event = {
      method: 'custom',
      params: { data: { name: 'moldy.subagent_names', payload: {} } },
    }

    expect(protocolSubagentNames(event)).toBeNull()
  })
})
