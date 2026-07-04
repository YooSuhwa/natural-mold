export interface SearchResultItem {
  title?: string
  url?: string
  link?: string
  snippet?: string
  content?: string
  description?: string
  score?: number
  published_date?: string
  /** 이미지/쇼핑 결과의 썸네일 URL (Naver thumbnail/image, Google image.thumbnailLink). */
  thumbnail?: string
  /** 쇼핑 결과의 최저가(KRW) — Naver lprice. */
  price?: number
  /** 쇼핑 결과의 판매처 — Naver mallName. */
  mall_name?: string
}

export interface SearchSourceSummary {
  title?: string
  url: string
  domain: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function parseJsonString(value: string): unknown {
  try {
    return JSON.parse(value) as unknown
  } catch {
    return undefined
  }
}

function thumbnailFrom(value: Record<string, unknown>): string | undefined {
  if (typeof value.thumbnail === 'string' && value.thumbnail) return value.thumbnail
  // Naver 쇼핑은 `image`가 썸네일 URL 문자열이다.
  if (typeof value.image === 'string' && value.image.startsWith('http')) return value.image
  // Google 이미지 검색은 `image: { thumbnailLink }` 객체다.
  if (isRecord(value.image) && typeof value.image.thumbnailLink === 'string') {
    return value.image.thumbnailLink
  }
  return undefined
}

function priceFrom(value: Record<string, unknown>): number | undefined {
  // Naver 쇼핑 lprice는 숫자 문자열("12900")로 온다.
  const raw = value.lprice
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw
  if (typeof raw === 'string' && /^\d+$/.test(raw)) return Number(raw)
  return undefined
}

function normalizeSearchItem(value: unknown): SearchResultItem | null {
  if (!isRecord(value)) return null
  const item: SearchResultItem = {}
  if (typeof value.title === 'string') item.title = value.title
  if (typeof value.url === 'string') item.url = value.url
  if (typeof value.link === 'string') item.link = value.link
  if (typeof value.snippet === 'string') item.snippet = value.snippet
  if (typeof value.content === 'string') item.content = value.content
  if (typeof value.description === 'string') item.description = value.description
  if (typeof value.score === 'number') item.score = value.score
  if (typeof value.published_date === 'string') item.published_date = value.published_date
  const thumbnail = thumbnailFrom(value)
  if (thumbnail) item.thumbnail = thumbnail
  const price = priceFrom(value)
  if (price !== undefined) item.price = price
  if (typeof value.mallName === 'string' && value.mallName) item.mall_name = value.mallName
  return Object.keys(item).length > 0 ? item : (value as SearchResultItem)
}

function normalizeSearchItems(value: unknown): SearchResultItem[] {
  if (!Array.isArray(value)) return []
  return value.map(normalizeSearchItem).filter((item): item is SearchResultItem => item !== null)
}

/**
 * MCP 도구 결과 래퍼(`[{type:'text', text:'<JSON>'}]`)를 벗겨 내부 JSON을
 * 돌려준다. 검색류 MCP 도구(예: Tavily MCP)가 이 shape로 결과를 싣는다.
 */
function unwrapMcpTextContent(raw: readonly unknown[]): unknown {
  const textBlock = raw.find(
    (item): item is Record<string, unknown> =>
      isRecord(item) && item.type === 'text' && typeof item.text === 'string',
  )
  if (!textBlock) return undefined
  return parseJsonString(textBlock.text as string)
}

/** 검색 결과 배열 추출 — Tavily/스크립트 도구는 `results`, Naver/Google은 `items`. */
function itemsArrayFrom(raw: Record<string, unknown>): unknown[] | null {
  if (Array.isArray(raw.results)) return raw.results
  if (Array.isArray(raw.items)) return raw.items
  return null
}

export function parseSearchResults(raw: unknown): SearchResultItem[] {
  if (!raw) return []

  if (typeof raw === 'string') {
    const parsed = parseJsonString(raw)
    if (parsed !== undefined) return parseSearchResults(parsed)
    return [{ snippet: raw }]
  }

  if (Array.isArray(raw)) {
    const unwrapped = unwrapMcpTextContent(raw)
    if (unwrapped !== undefined) return parseSearchResults(unwrapped)
    return normalizeSearchItems(raw)
  }

  if (isRecord(raw)) {
    const items = itemsArrayFrom(raw)
    if (items) return normalizeSearchItems(items)
    const item = normalizeSearchItem(raw)
    return item ? [item] : []
  }

  return []
}

/** Tavily `answer`(요약 답변) 추출 — 없으면 null. */
export function searchAnswerFromResult(raw: unknown): string | null {
  if (typeof raw === 'string') {
    const parsed = parseJsonString(raw)
    return parsed === undefined ? null : searchAnswerFromResult(parsed)
  }
  if (Array.isArray(raw)) {
    const unwrapped = unwrapMcpTextContent(raw)
    return unwrapped === undefined ? null : searchAnswerFromResult(unwrapped)
  }
  if (!isRecord(raw)) return null
  const answer = raw.answer
  return typeof answer === 'string' && answer.trim() ? answer : null
}

/**
 * 결과가 "검색 결과 shape"인지 감지 — 이름 매칭이 어긋난 검색 도구(사용자가
 * 이름을 바꾼 registry 도구, MCP 검색 도구)를 GenericToolFallback에서 리치
 * 카드로 라우팅하기 위한 보수적 판정. `results|items` 배열의 첫 레코드가
 * title과 url|link 문자열을 모두 가져야 true.
 */
export function looksLikeSearchResults(raw: unknown): boolean {
  if (typeof raw === 'string') {
    const parsed = parseJsonString(raw)
    return parsed === undefined ? false : looksLikeSearchResults(parsed)
  }
  if (Array.isArray(raw)) {
    const unwrapped = unwrapMcpTextContent(raw)
    return unwrapped === undefined ? false : looksLikeSearchResults(unwrapped)
  }
  if (!isRecord(raw)) return false
  const items = itemsArrayFrom(raw)
  if (!items || items.length === 0) return false
  const first = items.find(isRecord)
  if (!first) return false
  const hasTitle = typeof first.title === 'string' && first.title.length > 0
  const hasUrl =
    (typeof first.url === 'string' && first.url.length > 0) ||
    (typeof first.link === 'string' && first.link.length > 0)
  return hasTitle && hasUrl
}

export function searchItemUrl(item: SearchResultItem): string | undefined {
  return item.url ?? item.link
}

export function searchItemSnippet(item: SearchResultItem): string | undefined {
  return item.snippet ?? item.content ?? item.description
}

export function domainFromUrl(url: string): string {
  try {
    const hostname = new URL(url).hostname
    return hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

export function sourceSummariesFromResults(items: SearchResultItem[]): SearchSourceSummary[] {
  const seen = new Set<string>()
  const sources: SearchSourceSummary[] = []
  for (const item of items) {
    const url = searchItemUrl(item)
    if (!url || seen.has(url)) continue
    seen.add(url)
    sources.push({
      title: item.title,
      url,
      domain: domainFromUrl(url),
    })
  }
  return sources
}
