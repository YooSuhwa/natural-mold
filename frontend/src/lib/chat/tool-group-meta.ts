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
 * 검색류 도구. 결과가 `{results:[{title,url}]}` 모양이라 그룹 헤더에 출처
 * 도메인 배지 + "출처 N개" 집계를 띄울 수 있는 도구들. 나머지(파일류 등)는
 * 출처 개념이 없으므로 집계 행을 붙이지 않는다.
 */
const SEARCH_TOOLS: ReadonlySet<string> = new Set([
  'tavily_search',
  'web_search',
  'naver_blog_search',
  'naver_news_search',
  'google_search',
  'google_news_search',
])

/** 해당 도구가 검색류(그룹 헤더에 출처 집계를 띄울 수 있는 도구)인지. */
export function isSearchTool(toolName: string): boolean {
  return SEARCH_TOOLS.has(toolName)
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

/** 호출별 대표 인자 키 — 그룹 자식 제목에 쓸 "구분값"을 이 순서로 찾는다. */
const CHILD_LABEL_ARG_KEYS = [
  'query',
  'q',
  'file_path',
  'path',
  'url',
  'expression',
  'keyword',
  'date',
  'timezone',
  'command',
  'name',
] as const

function shortenLabel(value: string): string {
  const trimmed = value.trim().replace(/\s+/g, ' ')
  return trimmed.length > 48 ? `${trimmed.slice(0, 47)}…` : trimmed
}

/** JSON 값에서 첫 스칼라(문자열/숫자/불리언) 하나를 뽑는다 — raw JSON보다 읽기 좋게. */
function firstScalar(value: unknown): string | null {
  if (typeof value === 'string') return value.trim() || null
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) {
    for (const item of value) {
      const scalar = firstScalar(item)
      if (scalar) return scalar
    }
    return null
  }
  if (value && typeof value === 'object') {
    for (const item of Object.values(value)) {
      const scalar = firstScalar(item)
      if (scalar) return scalar
    }
  }
  return null
}

function resultPreview(result: unknown): string | null {
  if (typeof result !== 'string') return null
  const text = result.trim()
  if (!text) return null
  // JSON 객체/배열 결과면 대표 스칼라 하나를 뽑아 `{"now_iso": "…",` 대신 값만 표시.
  if (text.startsWith('{') || text.startsWith('[')) {
    try {
      const scalar = firstScalar(JSON.parse(text))
      if (scalar) return shortenLabel(scalar)
    } catch {
      // JSON 파싱 실패 → 아래 첫 줄 폴백.
    }
  }
  const firstLine = text.split('\n').find((line) => line.trim().length > 0)
  return firstLine ? shortenLabel(firstLine) : null
}

/**
 * 그룹 자식 pill의 제목 — 도구명(그룹 헤더에 이미 있음) 대신 "이 호출이 뭘 했나"를
 * 보여준다. 대표 인자 → 임의 첫 문자열 인자 → 결과 미리보기 순. 마땅한 게 없으면
 * null(호출 측이 도구명으로 폴백).
 */
export function toolCallChildLabel(
  args: Record<string, unknown> | undefined,
  result: unknown,
): string | null {
  for (const key of CHILD_LABEL_ARG_KEYS) {
    const value = args?.[key]
    if (typeof value === 'string' && value.trim()) return shortenLabel(value)
  }
  for (const value of Object.values(args ?? {})) {
    if (typeof value === 'string' && value.trim()) return shortenLabel(value)
  }
  return resultPreview(result)
}
