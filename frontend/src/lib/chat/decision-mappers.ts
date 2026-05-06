/**
 * 표준 ``Decision`` 객체 빌더 — HiTL ResumeRequest payload 의 4 액션 타입을
 * 인라인 리터럴 대신 일관된 helper 로 생성. 호출처에서 type 누락이나 잘못된
 * 필드 조합(예: respond 에 edited_action 첨부)을 컴파일 시점에 차단.
 *
 * ADR-012 §Decision schema 와 1:1 대응. Decision shape 변경 시 본 파일 한 곳
 * 만 수정하면 호출처 일괄 적용.
 */
import type { Decision } from '@/lib/types'

export function toApprove(): Decision {
  return { type: 'approve' }
}

export function toReject(message?: string): Decision {
  return message !== undefined ? { type: 'reject', message } : { type: 'reject' }
}

export function toEdit(editedAction: NonNullable<Decision['edited_action']>): Decision {
  return { type: 'edit', edited_action: editedAction }
}

export function toRespond(message: string): Decision {
  return { type: 'respond', message }
}
