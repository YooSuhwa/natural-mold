import { describe, expect, it } from 'vitest'
import { collectMatchRanges } from '../chat-search'

describe('collectMatchRanges', () => {
  it('매치 메시지별로 검색어 등장 Range를 수집한다(대소문자 무시)', () => {
    document.body.innerHTML = `
      <div data-moldy-message-id="m1">회의 준비와 회의록</div>
      <div data-moldy-message-id="m2">Hello WORLD</div>
      <div>앵커 없는 노드</div>
    `
    const map = collectMatchRanges('회의')
    expect(Array.from(map.keys())).toEqual(['m1'])
    // "회의"가 한 텍스트 노드에 2번 등장 → Range 2개.
    expect(map.get('m1')?.length).toBe(2)
    // 대소문자 무시.
    expect(Array.from(collectMatchRanges('world').keys())).toEqual(['m2'])
  })

  it('메타행/sr-only 텍스트는 검색에서 제외한다', () => {
    document.body.innerHTML = `
      <div data-moldy-message-id="m1">
        <div>본문 회의 내용</div>
        <div data-moldy-message-meta-row="true">복사 편집 회의</div>
        <span class="sr-only">회의 라벨</span>
      </div>
    `
    const map = collectMatchRanges('회의')
    // 본문의 "회의" 1개만 — 메타행/sr-only의 "회의"는 제외.
    expect(map.get('m1')?.length).toBe(1)
  })

  it('root로 스코프하면 root 밖 메시지는 제외한다(설정 페이지 이중 마운트 대비)', () => {
    document.body.innerHTML = `
      <div id="scope"><div data-moldy-message-id="m1">회의 A</div></div>
      <div data-moldy-message-id="m2">회의 B</div>
    `
    const scope = document.getElementById('scope')
    expect(scope).not.toBeNull()
    const map = collectMatchRanges('회의', scope as HTMLElement)
    expect(Array.from(map.keys())).toEqual(['m1'])
  })

  it('빈/공백 query는 빈 map', () => {
    document.body.innerHTML = `<div data-moldy-message-id="m1">회의</div>`
    expect(collectMatchRanges('').size).toBe(0)
    expect(collectMatchRanges('   ').size).toBe(0)
  })
})
