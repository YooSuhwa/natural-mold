import { describe, expect, it } from 'vitest'
import {
  collectMatchRanges,
  collectMessageEntries,
  filterMatchingIds,
  type MessageSearchEntry,
} from '../chat-search'

const entries: MessageSearchEntry[] = [
  { id: 'm1', text: '안녕하세요 반갑습니다' },
  { id: 'm2', text: 'Hello World' },
  { id: 'm3', text: '오늘 날씨가 좋네요' },
]

describe('filterMatchingIds', () => {
  it('대소문자 무시하고 부분 매치 id를 DOM 순서로 반환한다', () => {
    expect(filterMatchingIds(entries, 'hello')).toEqual(['m2'])
    expect(filterMatchingIds(entries, 'WORLD')).toEqual(['m2'])
    expect(filterMatchingIds(entries, '안녕')).toEqual(['m1'])
  })

  it('여러 매치를 순서대로 반환한다', () => {
    const multi: MessageSearchEntry[] = [
      { id: 'a', text: 'foo bar' },
      { id: 'b', text: 'baz' },
      { id: 'c', text: 'foo again' },
    ]
    expect(filterMatchingIds(multi, 'foo')).toEqual(['a', 'c'])
  })

  it('빈/공백 query는 빈 배열', () => {
    expect(filterMatchingIds(entries, '')).toEqual([])
    expect(filterMatchingIds(entries, '   ')).toEqual([])
  })

  it('매치 없으면 빈 배열', () => {
    expect(filterMatchingIds(entries, 'zzz-none')).toEqual([])
  })
})

describe('collectMessageEntries', () => {
  it('data-moldy-message-id 앵커 텍스트를 DOM 순서로 수집한다', () => {
    document.body.innerHTML = `
      <div data-moldy-message-id="m1">첫 번째 메시지</div>
      <div data-moldy-message-id="m2">두 번째 메시지</div>
      <div>앵커 없는 노드</div>
    `
    const collected = collectMessageEntries()
    expect(collected.map((entry) => entry.id)).toEqual(['m1', 'm2'])
    expect(collected[0].text).toContain('첫 번째 메시지')
  })
})

describe('collectMatchRanges', () => {
  it('매치 메시지별로 검색어 등장 Range를 수집한다', () => {
    document.body.innerHTML = `
      <div data-moldy-message-id="m1">회의 준비와 회의록</div>
      <div data-moldy-message-id="m2">다른 내용</div>
    `
    const map = collectMatchRanges('회의')
    expect(Array.from(map.keys())).toEqual(['m1'])
    // "회의"가 한 텍스트 노드에 2번 등장 → Range 2개.
    expect(map.get('m1')?.length).toBe(2)
  })

  it('빈/공백 query는 빈 map', () => {
    document.body.innerHTML = `<div data-moldy-message-id="m1">회의</div>`
    expect(collectMatchRanges('').size).toBe(0)
    expect(collectMatchRanges('   ').size).toBe(0)
  })
})
