/**
 * Decision 매퍼 회귀 가드 — ADR-012 §Decision schema 와 1:1 대응 contract.
 * Decision shape 변경 시 본 파일이 일괄 영향 범위.
 */
import { describe, expect, it } from 'vitest'
import { toApprove, toEdit, toReject, toRespond } from '../decision-mappers'

describe('toApprove', () => {
  it('순수 approve decision 반환 (다른 필드 없음)', () => {
    expect(toApprove()).toEqual({ type: 'approve' })
  })
})

describe('toReject', () => {
  it('message 인자 — type=reject + message', () => {
    expect(toReject('not allowed')).toEqual({ type: 'reject', message: 'not allowed' })
  })

  it('message 미지정 — type=reject (message 필드 부재)', () => {
    expect(toReject()).toEqual({ type: 'reject' })
  })

  it('빈 문자열 message 도 그대로 보존 (사용자 명시 의도)', () => {
    expect(toReject('')).toEqual({ type: 'reject', message: '' })
  })
})

describe('toEdit', () => {
  it('edited_action 그대로 포함', () => {
    const edited = { name: 'send_email', args: { to: 'a@b.c' } }
    expect(toEdit(edited)).toEqual({ type: 'edit', edited_action: edited })
  })

  it('빈 args 도 보존', () => {
    expect(toEdit({ name: 'noop', args: {} })).toEqual({
      type: 'edit',
      edited_action: { name: 'noop', args: {} },
    })
  })
})

describe('toRespond', () => {
  it('message 인자 — type=respond + message', () => {
    expect(toRespond('빨강')).toEqual({ type: 'respond', message: '빨강' })
  })

  it('빈 문자열 — message 필드는 빈 값 유지 (응답 결정은 명시적)', () => {
    expect(toRespond('')).toEqual({ type: 'respond', message: '' })
  })
})
