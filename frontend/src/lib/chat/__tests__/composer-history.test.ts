import { describe, expect, it } from 'vitest'
import {
  caretOnFirstLine,
  caretOnLastLine,
  collectUserHistory,
  extractMessageText,
  historyItemAt,
  stepHistoryIndex,
} from '../composer-history'

describe('extractMessageText', () => {
  it('문자열/텍스트 파트 배열에서 평문을 뽑는다', () => {
    expect(extractMessageText('안녕')).toBe('안녕')
    expect(
      extractMessageText([
        { type: 'text', text: '첫 줄' },
        { type: 'tool-call', toolName: 'x' },
        { type: 'text', text: '둘째 줄' },
      ]),
    ).toBe('첫 줄\n둘째 줄')
    expect(extractMessageText(undefined)).toBe('')
  })
})

describe('collectUserHistory', () => {
  it('user 메시지만 모으고 연속 중복·빈 항목을 제거한다', () => {
    const messages = [
      { role: 'user', content: '첫 질문' },
      { role: 'assistant', content: '답' },
      { role: 'user', content: '첫 질문' }, // 직전과 중복 아님? assistant 사이 → 히스토리상 연속
      { role: 'user', content: '   ' },
      { role: 'user', content: '둘째 질문' },
    ]
    expect(collectUserHistory(messages)).toEqual(['첫 질문', '둘째 질문'])
  })
})

describe('caret line 판정', () => {
  it('첫 줄/마지막 줄 캐럿을 구분한다', () => {
    const value = '첫 줄\n둘째 줄'
    expect(caretOnFirstLine(value, 2)).toBe(true)
    expect(caretOnFirstLine(value, value.length)).toBe(false)
    expect(caretOnLastLine(value, value.length)).toBe(true)
    expect(caretOnLastLine(value, 1)).toBe(false)
    // 빈 입력은 첫/마지막 줄 동시 성립.
    expect(caretOnFirstLine('', 0)).toBe(true)
    expect(caretOnLastLine('', 0)).toBe(true)
  })
})

describe('stepHistoryIndex + historyItemAt', () => {
  const history = ['오래된', '중간', '최신']

  it('↑는 최신부터 과거로, 경계에서 멈춘다', () => {
    expect(stepHistoryIndex(3, -1, 'up')).toBe(0)
    expect(stepHistoryIndex(3, 0, 'up')).toBe(1)
    expect(stepHistoryIndex(3, 2, 'up')).toBeNull()
    expect(historyItemAt(history, 0)).toBe('최신')
    expect(historyItemAt(history, 2)).toBe('오래된')
  })

  it('↓는 최신 방향으로, -1(draft 복원)까지 내려온다', () => {
    expect(stepHistoryIndex(3, 2, 'down')).toBe(1)
    expect(stepHistoryIndex(3, 0, 'down')).toBe(-1)
    expect(stepHistoryIndex(3, -1, 'down')).toBeNull()
    expect(historyItemAt(history, -1)).toBeNull()
  })

  it('빈 히스토리는 항상 null', () => {
    expect(stepHistoryIndex(0, -1, 'up')).toBeNull()
  })
})
