/**
 * 대화 내 검색 (G6). 렌더된 메시지의 DOM 앵커(``data-moldy-message-id``)에서
 * 텍스트를 수집해 클라이언트 필터한다 — v3 채팅의 메시지 소스(스트림/envelope)에
 * 무관하게 "화면에 보이는 텍스트"를 검색하고, 매치를 ``jumpToMessage``로 이동한다.
 */

export interface MessageSearchEntry {
  readonly id: string
  readonly text: string
}

/** DOM 순서(= 대화 순서)로 렌더된 메시지 엔트리를 수집한다. */
export function collectMessageEntries(root: ParentNode = document): MessageSearchEntry[] {
  const entries: MessageSearchEntry[] = []
  root.querySelectorAll('[data-moldy-message-id]').forEach((el) => {
    const id = el.getAttribute('data-moldy-message-id')
    if (id) entries.push({ id, text: (el as HTMLElement).textContent ?? '' })
  })
  return entries
}

/** query에 매치되는 메시지 id 목록(대소문자 무시, DOM 순서 보존). 빈 query면 빈 배열. */
export function filterMatchingIds(entries: readonly MessageSearchEntry[], query: string): string[] {
  const normalized = query.trim().toLowerCase()
  if (!normalized) return []
  return entries
    .filter((entry) => entry.text.toLowerCase().includes(normalized))
    .map((entry) => entry.id)
}
