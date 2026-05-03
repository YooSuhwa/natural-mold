'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { ExternalLinkIcon, GlobeIcon } from 'lucide-react'
import { CollapsiblePill, pillStatusFromAssistantUi } from './collapsible-pill'

// ──────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────

interface SearchArgs {
  query?: string
  [key: string]: unknown
}

interface SearchResultItem {
  title?: string
  url?: string
  link?: string
  snippet?: string
  content?: string
}

// ──────────────────────────────────────────────
// 결과 파서 — 문자열 / 배열 / 객체 모두 처리
// ──────────────────────────────────────────────

function parseSearchResults(raw: unknown): SearchResultItem[] {
  if (!raw) return []
  if (Array.isArray(raw)) return raw as SearchResultItem[]
  if (typeof raw === 'object') return [raw as SearchResultItem]
  if (typeof raw === 'string') {
    try {
      const parsed: unknown = JSON.parse(raw)
      if (Array.isArray(parsed)) return parsed as SearchResultItem[]
      if (typeof parsed === 'object' && parsed !== null) return [parsed as SearchResultItem]
    } catch {
      return [{ snippet: raw }]
    }
  }
  return []
}

// ──────────────────────────────────────────────
// SearchResultCard
// ──────────────────────────────────────────────

function SearchResultCard({ item }: { item: SearchResultItem }) {
  const url = item.url ?? item.link
  const title = item.title
  const snippet = item.snippet ?? item.content

  // 구조 없이 텍스트만 있는 경우
  if (!title && !url) {
    return (
      <div className="rounded-lg border border-border/40 bg-background p-2">
        <p className="text-[11px] leading-relaxed text-foreground/80 line-clamp-3">
          {snippet ?? JSON.stringify(item)}
        </p>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-border/40 bg-background p-2 transition-colors hover:bg-accent/50">
      <div className="flex items-start gap-2">
        <GlobeIcon className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          {title && (
            <div className="flex items-center gap-1">
              {url ? (
                <a
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="truncate text-[11px] font-medium text-primary-strong hover:underline"
                >
                  {title}
                </a>
              ) : (
                <span className="truncate text-[11px] font-medium">{title}</span>
              )}
              {url && <ExternalLinkIcon className="size-2.5 shrink-0 text-muted-foreground" />}
            </div>
          )}
          {url && <div className="truncate text-[10px] text-muted-foreground">{url}</div>}
          {snippet && (
            <p className="mt-0.5 text-[11px] leading-relaxed text-foreground/70 line-clamp-2">
              {snippet}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────
// 공유 render 함수
// ──────────────────────────────────────────────

function SearchRender({
  args,
  result,
  status,
}: {
  args: SearchArgs
  result?: unknown
  status: { readonly type: string }
}) {
  const isRunning = status.type === 'running'
  const items = parseSearchResults(result)
  const title = args?.query ? `"${args.query}"` : '웹 검색'
  const meta = isRunning ? '검색 중…' : items.length > 0 ? `${items.length}건` : undefined

  const body =
    !isRunning && items.length > 0 ? (
      <div className="space-y-1.5">
        {items.slice(0, 5).map((item, i) => (
          <SearchResultCard key={i} item={item} />
        ))}
      </div>
    ) : undefined

  return (
    <CollapsiblePill
      kind="tool"
      status={pillStatusFromAssistantUi(status.type)}
      title={title}
      meta={meta}
      defaultExpanded={!isRunning && items.length > 0}
    >
      {body}
    </CollapsiblePill>
  )
}

// ──────────────────────────────────────────────
// SearchToolUI — web_search + Naver + Google
// ──────────────────────────────────────────────

export const WebSearchToolUI = makeAssistantToolUI<SearchArgs, unknown>({
  toolName: 'web_search',
  render: SearchRender,
})

export const NaverBlogSearchToolUI = makeAssistantToolUI<SearchArgs, unknown>({
  toolName: 'naver_blog_search',
  render: SearchRender,
})

export const NaverNewsSearchToolUI = makeAssistantToolUI<SearchArgs, unknown>({
  toolName: 'naver_news_search',
  render: SearchRender,
})

export const GoogleSearchToolUI = makeAssistantToolUI<SearchArgs, unknown>({
  toolName: 'google_search',
  render: SearchRender,
})

export const GoogleNewsSearchToolUI = makeAssistantToolUI<SearchArgs, unknown>({
  toolName: 'google_news_search',
  render: SearchRender,
})
