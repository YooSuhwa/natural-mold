'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { ExternalLinkIcon, GlobeIcon, SearchIcon, SparklesIcon } from 'lucide-react'
import { CollapsiblePill, pillStatusFromAssistantUi } from './collapsible-pill'
import { useIsToolGroupChild } from './tool-group-child-context'
import {
  parseSearchResults,
  sanitizeThumbnailUrl,
  searchAnswerFromResult,
  searchItemSnippet,
  searchItemUrl,
  type SearchResultItem,
} from './search-tool-data'
import { formatDisplayNumber } from '@/lib/utils/display-format'

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
  const t = useTranslations('chat.toolCall.search')
  const url = searchItemUrl(item)
  const thumbnail = sanitizeThumbnailUrl(item.thumbnail)
  const title = item.title
  const snippet = searchItemSnippet(item)

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
        {thumbnail ? (
          // 검색 API 썸네일은 임의 원격 도메인이라 next/image 대신 일반 img를
          // lazy 로드로 사용한다. (스킴은 sanitizeThumbnailUrl로 제한.)
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={thumbnail}
            alt={title ?? t('thumbnailAlt')}
            loading="lazy"
            referrerPolicy="no-referrer"
            className="size-12 shrink-0 rounded-md border border-border/40 object-cover"
            data-moldy-search-thumbnail="true"
          />
        ) : (
          <GlobeIcon className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
        )}
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
          {item.price !== undefined && (
            <div className="mt-0.5 flex items-center gap-1.5" data-moldy-search-price="true">
              <span className="moldy-ui-caption font-semibold text-foreground">
                {t('price', { price: formatDisplayNumber(item.price) })}
              </span>
              {item.mall_name ? (
                <span className="moldy-ui-micro text-muted-foreground">{item.mall_name}</span>
              ) : null}
            </div>
          )}
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
// 공유 render 함수 — GenericToolFallback의 shape 라우팅에서도 재사용된다.
// ──────────────────────────────────────────────

export function SearchRender({
  args,
  result,
  status,
}: {
  args: SearchArgs
  result?: unknown
  status: { readonly type: string }
}) {
  const t = useTranslations('chat.toolCall.search')
  // 그룹 안의 검색 자식은 기본 접힘(쿼리 제목만) — N개가 모두 카드까지 펼쳐지면
  // 너무 길어진다. 단독 검색(그룹 아님)은 지금처럼 결과를 바로 펼친다.
  const isGroupChild = useIsToolGroupChild()
  const isRunning = status.type === 'running'
  const items = parseSearchResults(result)
  const answer = isRunning ? null : searchAnswerFromResult(result)
  const title = args?.query ? `"${args.query}"` : t('defaultTitle')
  const meta = isRunning
    ? t('running')
    : items.length > 0
      ? t('count', { count: items.length })
      : undefined

  const hasBody = !isRunning && (items.length > 0 || Boolean(answer))
  const body = hasBody ? (
    <div className="space-y-1.5">
      {answer ? (
        <div
          className="rounded-lg border border-primary-strong/25 bg-primary/5 p-2.5"
          data-moldy-search-answer="true"
        >
          <div className="mb-1 flex items-center gap-1.5 moldy-ui-micro font-semibold uppercase tracking-wider text-primary-strong">
            <SparklesIcon className="size-3" aria-hidden />
            {t('answer')}
          </div>
          <p className="moldy-ui-caption leading-relaxed text-foreground/85">{answer}</p>
        </div>
      ) : null}
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
      defaultExpanded={!isGroupChild && hasBody}
    >
      {body}
    </CollapsiblePill>
  )
}

// ──────────────────────────────────────────────
// SearchToolUI — web_search + Tavily + Naver + Google
//
// toolName은 런타임 이름과 일치해야 매칭된다. registry 도구의 런타임 이름은
// `_safe_tool_name(Tool.name || display_name, fallback=definition_key)`
// (backend tool_factory.py) — 한글 표시명은 새니타이즈에서 전부 소거되어
// definition_key로 폴백하므로 실제로는 definition_key(naver_search_blog 등)가
// 흐른다. 사용자가 도구 이름을 ASCII로 바꿔 이름이 어긋나는 경우는
// GenericToolFallback의 shape 기반 라우팅(looksLikeSearchResults)이 받는다.
// ──────────────────────────────────────────────

export const SEARCH_TOOL_UI_NAMES = [
  // builtin + 스킬 의존성(tavily_search) + E2E scripted
  'tavily_search',
  'web_search',
  // registry definition_key (Naver 5종)
  'naver_search_blog',
  'naver_search_news',
  'naver_search_image',
  'naver_search_shop',
  'naver_search_local',
  // registry definition_key (Google 3종)
  'google_search_web',
  'google_search_image',
  'google_search_news',
  // 과거 하드코딩 이름 — 실제 런타임 이름과 일치한 적은 없지만 기존 대화
  // 스냅샷/테스트 fixture 호환을 위해 유지한다.
  'naver_blog_search',
  'naver_news_search',
  'google_search',
  'google_news_search',
] as const

export const SEARCH_TOOL_UIS = SEARCH_TOOL_UI_NAMES.map((toolName) =>
  makeAssistantToolUI<SearchArgs, unknown>({ toolName, render: SearchRender }),
)
