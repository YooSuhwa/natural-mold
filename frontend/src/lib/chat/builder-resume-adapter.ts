/**
 * Builder v3 호환 resume 어댑터 — 표준 ``Decision[]`` 을 builder 의 자체 resume
 * 인자(아직 표준 wire 미지원, ADR-012 §Phase 5 까지 보존)로 변환.
 *
 * Builder 의 ``BuilderResumeRequest`` 는 native interrupt payload 의 직계 응답을
 * 받는다 — ``respond`` / ``reject`` 는 단순 message 문자열, 그 외(``approve`` /
 * ``edit``) 는 decision 객체 자체. ADR-012 §Phase 5 (옵션) 가 builder wire 를
 * 표준 ``decisions`` 로 통일하면 본 어댑터는 retire 가능.
 */
import type { Decision } from '@/lib/types'

export function decisionToBuilderResponse(decisions: Decision[]): unknown {
  const first = decisions[0]
  if (first?.type === 'respond' || first?.type === 'reject') {
    return first.message ?? ''
  }
  return first
}
