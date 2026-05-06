/**
 * ``decisionToBuilderResponse`` 회귀 가드 — Builder v3 호환 resume 어댑터.
 * ADR-012 §Phase 5 까지 보존되는 contract 이므로 builder wire 통일 PR 시
 * 본 가드와 함께 retire.
 */
import { describe, expect, it } from 'vitest'
import type { Decision } from '@/lib/types'
import { decisionToBuilderResponse } from '../builder-resume-adapter'

describe('decisionToBuilderResponse', () => {
  it('respond — message 문자열을 반환', () => {
    const decisions: Decision[] = [{ type: 'respond', message: '빨강' }]
    expect(decisionToBuilderResponse(decisions)).toBe('빨강')
  })

  it('respond — message 누락 시 빈 문자열 fallback', () => {
    const decisions: Decision[] = [{ type: 'respond' } as Decision]
    expect(decisionToBuilderResponse(decisions)).toBe('')
  })

  it('reject — message 문자열을 반환', () => {
    const decisions: Decision[] = [{ type: 'reject', message: '취소' }]
    expect(decisionToBuilderResponse(decisions)).toBe('취소')
  })

  it('reject — message 누락 시 빈 문자열 fallback', () => {
    const decisions: Decision[] = [{ type: 'reject' }]
    expect(decisionToBuilderResponse(decisions)).toBe('')
  })

  it('approve — decision 객체 자체를 반환', () => {
    const decisions: Decision[] = [{ type: 'approve' }]
    expect(decisionToBuilderResponse(decisions)).toEqual({ type: 'approve' })
  })

  it('edit — edited_action 포함한 decision 객체 반환', () => {
    const decision: Decision = {
      type: 'edit',
      edited_action: { name: 'send_email', args: { to: 'a@b.c' } },
    }
    expect(decisionToBuilderResponse([decision])).toEqual(decision)
  })

  it('multi-action 배열 — 첫 decision 만 사용 (builder 단일 응답 contract)', () => {
    const decisions: Decision[] = [
      { type: 'respond', message: '첫번째' },
      { type: 'approve' },
    ]
    expect(decisionToBuilderResponse(decisions)).toBe('첫번째')
  })

  it('빈 배열 — undefined 반환 (builder 호출처가 fallback 처리)', () => {
    expect(decisionToBuilderResponse([])).toBeUndefined()
  })
})
