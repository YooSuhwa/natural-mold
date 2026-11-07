import { describe, expect, it } from 'vitest'
import { protocolMemoryRecall } from '../memory-recall-events'

describe('protocolMemoryRecall', () => {
  it('moldy.memory_recalled custom 이벤트에서 brief 목록을 파싱한다', () => {
    const parsed = protocolMemoryRecall({
      method: 'custom',
      event_id: 'run-1:memory_recalled',
      params: {
        data: {
          name: 'moldy.memory_recalled',
          payload: {
            memories: [
              { id: 'm1', scope: 'user', content: '한국어 선호' },
              { id: 'm2', scope: 'agent', content: '표로 정리' },
            ],
          },
        },
      },
    })
    expect(parsed).toEqual([
      { id: 'm1', scope: 'user', content: '한국어 선호' },
      { id: 'm2', scope: 'agent', content: '표로 정리' },
    ])
  })

  it('custom:moldy.memory_recalled method 형태도 인식한다', () => {
    const parsed = protocolMemoryRecall({
      method: 'custom:moldy.memory_recalled',
      params: {
        data: { payload: { memories: [{ scope: 'user', content: '메모' }] } },
      },
    })
    expect(parsed).toEqual([{ id: undefined, scope: 'user', content: '메모' }])
  })

  it('scope나 content가 invalid한 항목은 걸러낸다', () => {
    const parsed = protocolMemoryRecall({
      method: 'custom',
      params: {
        data: {
          name: 'moldy.memory_recalled',
          payload: {
            memories: [
              { scope: 'user', content: '유효' },
              { scope: 'other', content: '스코프 불량' },
              { scope: 'agent', content: '   ' },
              'not-a-record',
            ],
          },
        },
      },
    })
    expect(parsed).toEqual([{ id: undefined, scope: 'user', content: '유효' }])
  })

  it('다른 custom 이벤트(subagent_names 등)는 null', () => {
    expect(
      protocolMemoryRecall({
        method: 'custom',
        params: { data: { name: 'moldy.subagent_names', payload: { names: {} } } },
      }),
    ).toBeNull()
    expect(
      protocolMemoryRecall({
        method: 'messages',
        params: { data: { chunk: 'x' } },
      }),
    ).toBeNull()
  })

  it('memories가 비어 있으면 null (칩 미표시)', () => {
    expect(
      protocolMemoryRecall({
        method: 'custom',
        params: { data: { name: 'moldy.memory_recalled', payload: { memories: [] } } },
      }),
    ).toBeNull()
  })

  it('경로와 프로필 줄 id를 읽고, id가 null인 프로필 brief와 경로 없는 옛 brief도 받는다', () => {
    const parsed = protocolMemoryRecall({
      method: 'custom',
      params: {
        data: {
          name: 'moldy.memory_recalled',
          payload: {
            memories: [
              {
                id: null,
                line_id: 'line-1',
                scope: 'user',
                content: '이름은 지수',
                path: 'profile',
              },
              { id: 'm2', scope: 'agent', content: '표로 정리', path: 'auto' },
              { id: 'm3', scope: 'user', content: 'KTX', path: 'tool' },
              { id: 'm4', scope: 'user', content: '옛 회상' },
              { id: 'm5', scope: 'user', content: '모르는 경로', path: 'mystery' },
            ],
          },
        },
      },
    })
    expect(parsed).toEqual([
      { lineId: 'line-1', scope: 'user', content: '이름은 지수', path: 'profile' },
      { id: 'm2', scope: 'agent', content: '표로 정리', path: 'auto' },
      { id: 'm3', scope: 'user', content: 'KTX', path: 'tool' },
      { id: 'm4', scope: 'user', content: '옛 회상' },
      { id: 'm5', scope: 'user', content: '모르는 경로' },
    ])
    expect(parsed?.[0].id).toBeUndefined()
    expect(parsed?.[3].path).toBeUndefined()
  })
})
