import { atom } from 'jotai'
import { atomWithStorage } from 'jotai/utils'

/**
 * Follow-up 고스트 제안 상태.
 *
 * - `followupEnabledAtom`: 켜고 끄는 옵션 — 대화 화면(컴포저 툴바)에서 토글하고
 *   localStorage에 영속된다(브라우저 전역 설정).
 * - `chatFollowupSuggestionAtom`: 대화별 현재 제안 1개. 런 종료 시
 *   use-followup-suggestion이 채우고, 새 런 시작·Esc 해제·수락 시 비운다.
 */

export const followupEnabledAtom = atomWithStorage<boolean>('moldy-followup-enabled', true)

export type ChatFollowupState = Record<string, string | null>

export const chatFollowupSuggestionAtom = atom<ChatFollowupState>({})

export const setConversationFollowupAtom = atom(
  null,
  (get, set, payload: { conversationId: string; suggestion: string | null }) => {
    const current = get(chatFollowupSuggestionAtom)
    set(chatFollowupSuggestionAtom, {
      ...current,
      [payload.conversationId]: payload.suggestion,
    })
  },
)
