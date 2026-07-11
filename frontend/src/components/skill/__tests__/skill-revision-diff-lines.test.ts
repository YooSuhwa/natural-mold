import { describe, expect, it } from 'vitest'

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

  it('멀티라인 chunk를 개별 라인으로 분해하고 말미 개행을 라인으로 세지 않는다', () => {
    const lines = computeRevisionDiffLines('x\n', 'x\ny\nz\n')

    expect(lines).toEqual([
      { type: 'context', text: 'x' },
      { type: 'added', text: 'y' },
      { type: 'added', text: 'z' },
    ])
  })
})
