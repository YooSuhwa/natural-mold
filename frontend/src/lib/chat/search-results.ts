export interface SearchResultItem {
  title?: string
  url?: string
  link?: string
  snippet?: string
  content?: string
  score?: number
  published_date?: string
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

function normalizeSearchItem(value: unknown): SearchResultItem | null {
  if (!isRecord(value)) return null
  const item: SearchResultItem = {}
  if (typeof value.title === 'string') item.title = value.title
  if (typeof value.url === 'string') item.url = value.url
  if (typeof value.link === 'string') item.link = value.link
  if (typeof value.snippet === 'string') item.snippet = value.snippet
  if (typeof value.content === 'string') item.content = value.content
  if (typeof value.score === 'number') item.score = value.score
  if (typeof value.published_date === 'string') item.published_date = value.published_date
  return Object.keys(item).length > 0 ? item : (value as SearchResultItem)
}

function normalizeSearchItems(value: unknown): SearchResultItem[] {
  if (!Array.isArray(value)) return []
  return value.map(normalizeSearchItem).filter((item): item is SearchResultItem => item !== null)
}

export function parseSearchResults(raw: unknown): SearchResultItem[] {
  if (!raw) return []

  if (typeof raw === 'string') {
    const parsed = parseJsonString(raw)
    if (parsed !== undefined) return parseSearchResults(parsed)
    return [{ snippet: raw }]
  }

  if (Array.isArray(raw)) return normalizeSearchItems(raw)

  if (isRecord(raw)) {
    if (Array.isArray(raw.results)) return normalizeSearchItems(raw.results)
    const item = normalizeSearchItem(raw)
    return item ? [item] : []
  }

  return []
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
    const url = item.url ?? item.link
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
