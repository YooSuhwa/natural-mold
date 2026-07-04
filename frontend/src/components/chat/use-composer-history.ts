'use client'

import { useCallback, useEffect, useMemo, useRef, type KeyboardEvent } from 'react'
import { useAui, useAuiState } from '@assistant-ui/react'
import {
  caretOnFirstLine,
  caretOnLastLine,
  collectUserHistory,
  historyItemAt,
  stepHistoryIndex,
} from '@/lib/chat/composer-history'
import { focusTextareaAtEnd } from './composer-focus'

// useAuiState(useSyncExternalStore, Object.is)는 selector가 매 snapshot 새
// 배열을 돌려주면 무한 리렌더가 난다(tool-group-container.tsx의 확립된 함정).
// 히스토리는 구분자로 join한 시그니처 문자열로 안정화하고 useMemo로 되살린다.
// U+0000은 채팅 입력에 나타날 수 없는 제어문자다.
const HISTORY_SEP = '\u0000'

/**
 * ↑/↓ 컴포저 입력 히스토리 (readline 스타일).
 *
 * - ↑는 캐럿이 첫 줄일 때만, ↓는 마지막 줄일 때만 히스토리로 승격 —
 *   그 외에는 일반 커서 이동을 그대로 둔다(멀티라인 안전).
 * - 탐색 진입 시 작성 중이던 draft를 보관하고, ↓로 최신을 지나 내려오면 복원.
 * - 불러온 항목을 편집하면(외부 setText 포함) 탐색 상태를 리셋한다.
 * - IME 조합 중(isComposing)에는 개입하지 않는다 — 한국어 입력 안전.
 *
 * 히스토리 소스는 현재 스레드의 user 메시지라 리로드 후에도 유지된다.
 */
export function useComposerHistory(conversationId: string | null): {
  handleHistoryKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void
} {
  const aui = useAui()
  const historySig = useAuiState((state) =>
    collectUserHistory(state.thread.messages).join(HISTORY_SEP),
  )
  const history = useMemo(() => (historySig ? historySig.split(HISTORY_SEP) : []), [historySig])
  const composerText = useAuiState((state) => (state.composer.isEditing ? state.composer.text : ''))

  const indexRef = useRef(-1)
  const draftRef = useRef('')
  const lastAppliedRef = useRef<string | null>(null)

  // 대화 전환 시 탐색 상태 초기화.
  useEffect(() => {
    indexRef.current = -1
    draftRef.current = ''
    lastAppliedRef.current = null
  }, [conversationId])

  // 사용자가 항목을 편집(또는 전송으로 비워짐)하면 탐색 이탈 — readline 단순형.
  useEffect(() => {
    if (lastAppliedRef.current !== null && composerText !== lastAppliedRef.current) {
      indexRef.current = -1
      lastAppliedRef.current = null
    }
  }, [composerText])

  const applyText = useCallback(
    (textarea: HTMLTextAreaElement, next: string) => {
      lastAppliedRef.current = next
      aui.composer().setText(next)
      // setText → 외부값 동기화 이후 캐럿을 끝으로.
      requestAnimationFrame(() => focusTextareaAtEnd(textarea))
    },
    [aui],
  )

  const handleHistoryKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.defaultPrevented) return
      if (event.nativeEvent.isComposing) return
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
      if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return
      if (history.length === 0) return

      const textarea = event.currentTarget
      const direction = event.key === 'ArrowUp' ? 'up' : 'down'
      if (direction === 'up' && !caretOnFirstLine(textarea.value, textarea.selectionStart)) return
      if (direction === 'down' && !caretOnLastLine(textarea.value, textarea.selectionEnd)) return

      const nextIndex = stepHistoryIndex(history.length, indexRef.current, direction)
      if (nextIndex === null) return

      event.preventDefault()
      if (indexRef.current === -1) {
        // 탐색 진입 — 작성 중이던 내용을 보관.
        draftRef.current = textarea.value
      }
      indexRef.current = nextIndex
      const item = historyItemAt(history, nextIndex)
      applyText(textarea, item ?? draftRef.current)
    },
    [applyText, history],
  )

  return { handleHistoryKeyDown }
}
