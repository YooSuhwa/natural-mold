import { atom } from 'jotai'

/**
 * 스킬 빌더 챗 검증 레일 스토어 (스펙 AD-5).
 *
 * `moldy.skill_draft`(stream-head 드래프트 요약) / `moldy.skill_validation`
 * (validate_skill·finalize_skill 도구 projection) custom 이벤트를
 * conversationId 스코프로 보관한다 — chat-subagent-names 패턴 미러.
 * 페이로드는 요약 전용 계약(파일 경로/크기/카운트 + 검증 이슈) — 파일 내용 없음.
 */

export interface SkillDraftBriefFile {
  readonly path: string
  readonly size: number
}

export interface SkillDraftBrief {
  readonly session_id: string
  readonly mode: string
  readonly slug: string | null
  readonly file_count: number
  readonly files: readonly SkillDraftBriefFile[]
  readonly changed_count: number
  /** 드래프트 moldy.yaml의 credential 요구 수 (상태 카드 행, M7). */
  readonly credential_requirement_count: number
}

export interface SkillValidationSnapshot {
  readonly tool_name: string
  readonly session_id?: string
  readonly validation_result: Readonly<Record<string, unknown>>
}

/** conversationId → 최신 드래프트 요약 (run마다 최신으로 교체). */
export const chatSkillDraftBriefAtom = atom<Record<string, SkillDraftBrief>>({})

export const setConversationSkillDraftBriefAtom = atom(
  null,
  (get, set, update: { readonly conversationId: string; readonly brief: SkillDraftBrief }) => {
    const current = get(chatSkillDraftBriefAtom)
    set(chatSkillDraftBriefAtom, { ...current, [update.conversationId]: update.brief })
  },
)

/** conversationId → 최신 검증 결과 projection (validate/finalize 도구 결과). */
export const chatSkillValidationAtom = atom<Record<string, SkillValidationSnapshot>>({})

export const setConversationSkillValidationAtom = atom(
  null,
  (
    get,
    set,
    update: {
      readonly conversationId: string
      readonly snapshot: SkillValidationSnapshot
    },
  ) => {
    const current = get(chatSkillValidationAtom)
    set(chatSkillValidationAtom, { ...current, [update.conversationId]: update.snapshot })
  },
)
