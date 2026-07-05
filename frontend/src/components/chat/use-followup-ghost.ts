'use client'

import { useCallback, type KeyboardEvent, type RefObject } from 'react'
import { useAtomValue, useSetAtom } from 'jotai'
import { useAui, useAuiState } from '@assistant-ui/react'
import {
  chatFollowupSuggestionAtom,
  followupEnabledAtom,
  setConversationFollowupAtom,
} from '@/lib/stores/chat-followup'
import { focusTextareaAtEnd } from './composer-focus'

/**
 * Follow-up 고스트(연한 제안 텍스트) 상호작용 — fish autosuggestion 계약.
 *
 * - 표시 조건: 토글 ON + 제안 존재 + 컴포저 비어 있음 + 런 미진행.
 *   (타이핑이 시작되면 컴포저가 비어 있지 않으므로 자동으로 사라진다.)
 * - → 또는 End: 제안을 실제 입력으로 채운다(전송은 Enter로 별도).
 * - Esc: 이번 제안 해제.
 * - IME 조합 중에는 개입하지 않는다.
 */
export function useFollowupGhost(
  conversationId: string | null,
  textareaRef: RefObject<HTMLTextAreaElement | null>,
): {
  ghostText: string | null
  handleGhostKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void
  acceptGhost: () => void
} {
  const aui = useAui()
  const enabled = useAtomValue(followupEnabledAtom)
  const suggestions = useAtomValue(chatFollowupSuggestionAtom)
  const setFollowup = useSetAtom(setConversationFollowupAtom)
  const composerEmpty = useAuiState(
    (state) => !state.composer.isEditing || state.composer.text.trim() === '',
  )
  const isRunning = useAuiState((state) => state.thread.isRunning)

  const suggestion = conversationId ? (suggestions[conversationId] ?? null) : null
  const ghostText = enabled && composerEmpty && !isRunning ? suggestion : null

  const acceptGhost = useCallback(() => {
    if (!ghostText || !conversationId) return
    aui.composer().setText(ghostText)
    // 수락된 제안은 소진 — 같은 제안이 비운 뒤 다시 뜨지 않게.
    setFollowup({ conversationId, suggestion: null })
    requestAnimationFrame(() => focusTextareaAtEnd(textareaRef.current))
  }, [aui, conversationId, ghostText, setFollowup, textareaRef])

  const handleGhostKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.defaultPrevented) return
      if (event.nativeEvent.isComposing) return
      if (!ghostText || !conversationId) return
      if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return

      if (event.key === 'ArrowRight' || event.key === 'End') {
        event.preventDefault()
        acceptGhost()
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setFollowup({ conversationId, suggestion: null })
      }
    },
    [acceptGhost, conversationId, ghostText, setFollowup],
  )

  return { ghostText, handleGhostKeyDown, acceptGhost }
}
