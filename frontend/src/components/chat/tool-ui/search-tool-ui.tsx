'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { ExternalLinkIcon, GlobeIcon, SearchIcon } from 'lucide-react'
import { CollapsiblePill, pillStatusFromAssistantUi } from './collapsible-pill'
import { parseSearchResults, type SearchResultItem } from './search-tool-data'

// ──────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────

interface SearchArgs {
  query?: string
  [key: string]: unknown
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
        <p className="moldy-ui-caption leading-relaxed text-foreground/80 line-clamp-3">
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
                  className="truncate moldy-ui-caption font-medium text-primary-strong hover:underline"
                >
                  {title}
                </a>
              ) : (
                <span className="truncate moldy-ui-caption font-medium">{title}</span>
              )}
              {url && <ExternalLinkIcon className="size-2.5 shrink-0 text-muted-foreground" />}
            </div>
          )}
          {url && <div className="truncate moldy-ui-micro text-muted-foreground">{url}</div>}
          {snippet && (
            <p className="mt-0.5 moldy-ui-caption leading-relaxed text-foreground/70 line-clamp-2">
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
  const t = useTranslations('chat.toolCall.search')
  const isRunning = status.type === 'running'
  const items = parseSearchResults(result)
  const title = args?.query ? `"${args.query}"` : t('defaultTitle')
  const meta = isRunning
    ? t('running')
    : items.length > 0
      ? t('count', { count: items.length })
      : undefined

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
      leadingIcon={SearchIcon}
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

export const TavilySearchToolUI = makeAssistantToolUI<SearchArgs, unknown>({
  toolName: 'tavily_search',
  render: SearchRender,
})

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
