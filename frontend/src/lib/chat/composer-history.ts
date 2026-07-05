/**
 * 컴포저 입력 히스토리(↑/↓) — readline 스타일의 순수 로직.
 *
 * 히스토리 소스는 현재 스레드의 user 메시지(서버 영속 → 리로드 생존)이고,
 * 탐색 상태는 훅(use-composer-history)이 ref로 든다. 이 모듈은 텍스트 추출·
 * dedupe·캐럿 줄 판정·인덱스 스텝만 담당해 유닛 테스트를 단순하게 만든다.
 */

interface MessageLike {
  readonly role?: unknown
  readonly content?: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** assistant-ui ThreadMessage content(문자열 | 파트 배열)에서 평문 텍스트 추출. */
export function extractMessageText(content: unknown): string {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .map((part) => {
      if (typeof part === 'string') return part
      if (isRecord(part) && part.type === 'text' && typeof part.text === 'string') {
        return part.text
      }
      return ''
    })
    .filter(Boolean)
    .join('\n')
}

/**
 * user 메시지 → 히스토리 목록(오래된 것 → 최신). 빈 항목 제외 +
 * 연속 중복 제거(HISTCONTROL ignoredups). 입력이 배열이 아니면(부분 mock 등)
 * 빈 히스토리로 안전 폴백한다.
 */
export function collectUserHistory(messages: unknown): string[] {
  if (!Array.isArray(messages)) return []
  const history: string[] = []
  for (const message of messages) {
    if (!isRecord(message) || (message as MessageLike).role !== 'user') continue
    const text = extractMessageText((message as MessageLike).content).trim()
    if (!text) continue
    if (history[history.length - 1] === text) continue
    history.push(text)
  }
  return history
}

/** 캐럿이 첫 줄에 있는가 — ↑를 히스토리 탐색으로 승격할 조건. */
export function caretOnFirstLine(value: string, selectionStart: number): boolean {
  return !value.slice(0, selectionStart).includes('\n')
}

/** 캐럿이 마지막 줄에 있는가 — ↓를 히스토리 탐색으로 승격할 조건. */
export function caretOnLastLine(value: string, selectionEnd: number): boolean {
  return !value.slice(selectionEnd).includes('\n')
}

/**
 * 히스토리 인덱스 스텝. index는 -1(탐색 안 함) / 0(최신) / 1(그 이전)…
 * 경계를 넘으면 null(이동 없음).
 */
export function stepHistoryIndex(
  historyLength: number,
  index: number,
  direction: 'up' | 'down',
): number | null {
  if (historyLength === 0) return null
  if (direction === 'up') {
    const next = index + 1
    return next < historyLength ? next : null
  }
  if (index <= -1) return null
  return index - 1
}

/** index가 가리키는 히스토리 항목(-1이면 null = draft 복원 지점). */
export function historyItemAt(history: readonly string[], index: number): string | null {
  if (index < 0 || index >= history.length) return null
  return history[history.length - 1 - index]
}
