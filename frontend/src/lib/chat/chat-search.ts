/**
 * 대화 내 검색 (G6). 렌더된 메시지의 DOM 앵커(``data-moldy-message-id``)에서
 * 검색어 위치를 Range로 수집해 CSS Custom Highlight API로 하이라이트하고, 매치를
 * ``jumpToMessage``로 이동한다. v3 채팅의 메시지 소스(스트림/envelope)에 무관하게
 * "화면에 보이는 본문"을 검색한다. ``root``로 특정 thread viewport에 스코프한다.
 */

const HIGHLIGHT_MATCH = 'moldy-search-match'
const HIGHLIGHT_CURRENT = 'moldy-search-current'

/** 메시지 본문이 아닌 텍스트는 검색에서 제외한다: 메타행(복사/편집/브랜치 피커/
 *  타임스탬프/토큰 수)과 sr-only 라벨. 안 그러면 "복사"/"편집"이 모든 메시지를,
 *  숫자가 메타를 매치시켜 카운트가 부풀고 보이지 않는 텍스트로 점프한다. */
function isNonBodyText(node: Node): boolean {
  const parent = node.parentElement
  if (!parent) return false
  return (
    parent.closest('[data-moldy-message-meta-row="true"]') !== null ||
    parent.closest('.sr-only') !== null
  )
}

function matchRangesInElement(element: Element, needle: string): Range[] {
  const ranges: Range[] = []
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) =>
      isNonBodyText(node) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  })
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
 * (대소문자 무시, 텍스트 노드 단위 — 마크다운 렌더로 분할된 노드에 걸친 구문은 놓침).
 * ``root``를 넘겨 해당 thread viewport로 스코프한다(설정 페이지의 이중 마운트 대비).
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
