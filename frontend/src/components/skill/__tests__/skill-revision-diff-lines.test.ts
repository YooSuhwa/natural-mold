import { describe, expect, it } from 'vitest'

import { computeCappedRevisionDiffLines, countInputLines } from '../skill-revision-diff-card'
import { computeRevisionDiffLines, hasRevisionDiffChanges } from '../skill-revision-diff-lines'

describe('computeRevisionDiffLines', () => {
  it('추가/삭제/문맥 라인을 평탄화한다', () => {
    const before = 'line-1\nline-2\nline-3\n'
    const after = 'line-1\nline-2-changed\nline-3\n'

    const lines = computeRevisionDiffLines(before, after)

    expect(lines).toEqual([
      { type: 'context', text: 'line-1' },
      { type: 'removed', text: 'line-2' },
      { type: 'added', text: 'line-2-changed' },
      { type: 'context', text: 'line-3' },
    ])
    expect(hasRevisionDiffChanges(lines)).toBe(true)
  })

  it('동일 콘텐츠는 문맥 라인만 반환한다', () => {
    const text = 'same\ncontent\n'
    const lines = computeRevisionDiffLines(text, text)

    expect(lines.every((line) => line.type === 'context')).toBe(true)
    expect(hasRevisionDiffChanges(lines)).toBe(false)
  })

  it('빈 원본 대비는 전부 추가 라인 (최초 리비전 계약)', () => {
    const lines = computeRevisionDiffLines('', 'a\nb\n')

    expect(lines).toEqual([
      { type: 'added', text: 'a' },
      { type: 'added', text: 'b' },
    ])
  })

  it('CRLF↔LF 개행 차이는 변경으로 세지 않는다 (stripTrailingCr)', () => {
    const lines = computeRevisionDiffLines('a\r\nb\r\n', 'a\nb\n')

    expect(hasRevisionDiffChanges(lines)).toBe(false)
  })

  it('말미 개행 유무만 다른 파일은 마지막 라인을 유령 변경으로 만들지 않는다', () => {
    // jsdiff는 'b'와 'b\n'을 같은 라인으로 취급(newline 차이는 변경 아님).
    const lines = computeRevisionDiffLines('a\nb', 'a\nb\n')

    expect(hasRevisionDiffChanges(lines)).toBe(false)
  })

  it('멀티라인 chunk를 개별 라인으로 분해하고 말미 개행을 라인으로 세지 않는다', () => {
    const lines = computeRevisionDiffLines('x\n', 'x\ny\nz\n')

    expect(lines).toEqual([
      { type: 'context', text: 'x' },
      { type: 'added', text: 'y' },
      { type: 'added', text: 'z' },
    ])
  })
})

describe('countInputLines (diff 사전 검사, R5)', () => {
  // O(ND) Myers 이전의 싼 상한 검사 — diff 출력 라인 수는 max(입력 라인) 이상
  // 이므로 한쪽 입력만으로 상한 초과가 확정이면 diff를 건너뛴다.
  it('개행 기준 라인 수를 할당 없이 센다', () => {
    expect(countInputLines('')).toBe(1)
    expect(countInputLines('a')).toBe(1)
    expect(countInputLines('a\nb')).toBe(2)
    expect(countInputLines('a\nb\n')).toBe(3)
  })

  it('병적 입력(수만 라인)도 즉시 계수한다', () => {
    const huge = 'x\n'.repeat(200_000)
    const start = performance.now()
    expect(countInputLines(huge)).toBe(200_001)
    expect(performance.now() - start).toBeLessThan(200)
  })
})

describe('computeCappedRevisionDiffLines (검사 순서 계약, R6)', () => {
  it('동일 텍스트는 상한 초과 크기여도 "변경 없음"(빈 배열)이다 — tooLarge 오표기 금지', () => {
    // 6천 라인 무변경 롤백 리비전 — 사전 검사가 먼저면 "변경이 너무 큼"으로
    // 거짓 표기된다.
    const huge = 'line\n'.repeat(6_000)
    expect(computeCappedRevisionDiffLines(huge, huge)).toEqual([])
  })

  it('한쪽 입력이 상한을 넘고 내용이 다르면 diff 없이 null(placeholder)', () => {
    const huge = 'line\n'.repeat(6_000)
    expect(computeCappedRevisionDiffLines(huge, 'other\n')).toBeNull()
    expect(computeCappedRevisionDiffLines('other\n', huge)).toBeNull()
  })

  it('상한 내 변경은 정상 diff 라인을 반환한다', () => {
    const lines = computeCappedRevisionDiffLines('a\n', 'b\n')
    expect(lines).toEqual([
      { type: 'removed', text: 'a' },
      { type: 'added', text: 'b' },
    ])
  })
})
