/**
 * Tool-call 그룹 컨테이너 메타.
 *
 * 범용 그룹핑(`MessagePrimitive.GroupedParts`)에서 연속 같은 도구를 1개
 * 컨테이너로 묶을 때 쓰는 라벨/그룹가능 여부를 정의한다. 그룹핑 로직 자체는
 * 공식 API가 담당하고, 이 모듈은 "도구별 표시 라벨"과 "그룹 제외 대상"만 책임진다.
 */

/**
 * toolName → `chat.toolGroup.labels.*` i18n 키. 매핑이 없으면 `null`을 반환하고,
 * 호출 측은 toolName 자체를 fallback 라벨로 쓴다. (검색류/파일류만 매핑 —
 * 나머지는 그룹 빈도가 낮아 toolName fallback으로 충분.)
 */
const TOOL_LABEL_KEYS: Readonly<Record<string, string>> = {
  tavily_search: 'webSearch',
  web_search: 'webSearch',
  naver_blog_search: 'naverBlog',
  naver_news_search: 'naverNews',
  google_search: 'googleSearch',
  google_news_search: 'googleNews',
  read_file: 'readFile',
  write_file: 'writeFile',
  edit_file: 'editFile',
}

/** toolName의 그룹 라벨 i18n 키. 없으면 null(호출 측에서 toolName fallback). */
export function toolGroupLabelKey(toolName: string): string | null {
  return TOOL_LABEL_KEYS[toolName] ?? null
}

/**
 * 그룹핑에서 제외할 도구. HiTL/승인처럼 각 호출이 사용자와의 독립 상호작용이라
 * 하나로 뭉치면 안 되는 도구들. 나머지 일반 도구는 연속 N≥2면 그룹된다.
 */
const NON_GROUPABLE_TOOLS: ReadonlySet<string> = new Set([
  'ask_user',
  'ask_clarifying_question',
  'request_approval',
])

/** 해당 도구를 그룹 컨테이너로 묶어도 되는지. */
export function isGroupableTool(toolName: string): boolean {
  return !NON_GROUPABLE_TOOLS.has(toolName)
}
