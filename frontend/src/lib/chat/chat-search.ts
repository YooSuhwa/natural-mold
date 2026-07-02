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

const HIGHLIGHT_MATCH = 'moldy-search-match'
const HIGHLIGHT_CURRENT = 'moldy-search-current'

function matchRangesInElement(element: Element, needle: string): Range[] {
  const ranges: Range[] = []
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
  let node = walker.nextNode()
  while (node) {
    const lower = (node.textContent ?? '').toLowerCase()
    let index = lower.indexOf(needle)
    while (index !== -1) {
      const range = document.createRange()
      range.setStart(node, index)
      range.setEnd(node, index + needle.length)
      ranges.push(range)
      index = lower.indexOf(needle, index + needle.length)
    }
    node = walker.nextNode()
  }
  return ranges
}

/**
 * 메시지 앵커 내부에서 query가 등장하는 각 위치의 Range를 messageId별로 수집한다
 * (대소문자 무시, 텍스트 노드 단위 — 마크다운 렌더로 분할된 노드는 개별 매치).
 * 반환 map의 key 집합 = 매치 메시지 id(DOM 순서).
 */
export function collectMatchRanges(
  query: string,
  root: ParentNode = document,
): Map<string, Range[]> {
  const map = new Map<string, Range[]>()
  const needle = query.trim().toLowerCase()
  if (!needle) return map
  root.querySelectorAll('[data-moldy-message-id]').forEach((el) => {
    const id = el.getAttribute('data-moldy-message-id')
    if (!id) return
    const ranges = matchRangesInElement(el, needle)
    if (ranges.length > 0) map.set(id, ranges)
  })
  return map
}

function highlightApiSupported(): boolean {
  return (
    typeof Highlight !== 'undefined' && typeof CSS !== 'undefined' && CSS.highlights !== undefined
  )
}

/**
 * CSS Custom Highlight API로 검색어를 인라인 하이라이트한다. 현재 매치 메시지의
 * Range는 ``moldy-search-current``, 나머지 매치는 ``moldy-search-match``로 등록한다.
 * 미지원 브라우저에서는 no-op이며 점프 하이라이트(moldy-jump-highlight)만 동작한다.
 */
export function applySearchHighlights(
  rangeMap: ReadonlyMap<string, readonly Range[]>,
  currentId: string | undefined,
): void {
  if (!highlightApiSupported()) return
  const matchHighlight = new Highlight()
  const currentHighlight = new Highlight()
  for (const [id, ranges] of rangeMap) {
    const target = id === currentId ? currentHighlight : matchHighlight
    for (const range of ranges) target.add(range)
  }
  CSS.highlights.set(HIGHLIGHT_MATCH, matchHighlight)
  CSS.highlights.set(HIGHLIGHT_CURRENT, currentHighlight)
}

export function clearSearchHighlights(): void {
  if (!highlightApiSupported()) return
  CSS.highlights.delete(HIGHLIGHT_MATCH)
  CSS.highlights.delete(HIGHLIGHT_CURRENT)
}
