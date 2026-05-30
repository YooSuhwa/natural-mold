'use client'

import { Fragment } from 'react'
import { usePathname } from 'next/navigation'
import { ChevronRightIcon, HomeIcon } from 'lucide-react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { useAgent } from '@/lib/hooks/use-agents'
import { useConversations } from '@/lib/hooks/use-conversations'
import { useMarketplaceItem } from '@/lib/hooks/use-marketplace'

const ROUTE_LABELS: Record<string, string> = {
  tools: 'nav.tools',
  models: 'nav.models',
  schedules: 'nav.schedules',
  usage: 'nav.usage',
  settings: 'nav.settings',
  skills: 'nav.skills',
  new: 'nav.createAgent',
  'visual-settings': 'nav.visualSettings',
  conversational: 'nav.conversational',
  manual: 'nav.manual',
  template: 'nav.template',
}

// Segments that never have their own page — always skip
const SKIP_SEGMENTS = new Set(['agents', 'conversations'])
// (no conditional skip — "new" always shown for context)

function AgentName({ id }: { id: string }) {
  const { data: agent } = useAgent(id)
  return <>{agent?.name ?? id}</>
}

function ConversationTitle({
  agentId,
  conversationId,
}: {
  agentId: string
  conversationId: string
}) {
  const { data: conversations } = useConversations(agentId)
  const conv = conversations?.find((c) => c.id === conversationId)
  return <>{conv?.title ?? conversationId}</>
}

function MarketplaceItemName({ id }: { id: string }) {
  // 404 / 401 시 hook 이 ApiError 를 throw 하므로 breadcrumb 가 빈 ID 로 떨어지면
  // ``id`` fallback. detail page 에서 동일 query 가 이미 cache 되어 있어 추가
  // round-trip 없음 (TanStack Query 자동 dedup).
  const { data: item } = useMarketplaceItem(id)
  return <>{item?.name ?? id}</>
}

export function BreadcrumbNav() {
  const pathname = usePathname()
  const t = useTranslations()
  const ta = useTranslations('common.a11y')
  const segments = pathname.split('/').filter(Boolean)

  if (segments.length === 0) return null

  // Build crumbs, skipping segments that have no page
  // Also find agentId for resolving conversation titles
  let agentId: string | null = null
  const crumbs: {
    label: string
    href: string
    isLast: boolean
    isAgentId: boolean
    isConvId: boolean
    isMarketplaceItemId: boolean
  }[] = []

  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i]
    const href = '/' + segments.slice(0, i + 1).join('/')
    const isId = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(segment)

    // Track agentId (first ID after 'agents')
    if (isId && i > 0 && segments[i - 1] === 'agents') {
      agentId = segment
    }

    // Skip segments without real pages
    if (SKIP_SEGMENTS.has(segment)) continue

    const isLast = i === segments.length - 1
    const isAgentId = isId && i > 0 && segments[i - 1] === 'agents'
    const isConvId = isId && i > 0 && segments[i - 1] === 'conversations'
    const isMarketplaceItemId = isId && i > 0 && segments[i - 1] === 'marketplace'

    let label = segment
    if (!isId && ROUTE_LABELS[segment]) {
      label = t(ROUTE_LABELS[segment])
    }

    crumbs.push({ label, href, isLast, isAgentId, isConvId, isMarketplaceItemId })
  }

  return (
    <nav aria-label={ta('breadcrumb')} className="flex items-center gap-1.5 text-sm min-w-0">
      <Link
        href="/"
        className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
      >
        <HomeIcon className="size-4" />
      </Link>

      {crumbs.map((crumb) => {
        const crumbContent = crumb.isAgentId ? (
          <AgentName id={crumb.label} />
        ) : crumb.isConvId && agentId ? (
          <ConversationTitle agentId={agentId} conversationId={crumb.label} />
        ) : crumb.isMarketplaceItemId ? (
          <MarketplaceItemName id={crumb.label} />
        ) : (
          crumb.label
        )

        return (
          <Fragment key={crumb.href}>
            <ChevronRightIcon
              className="size-3.5 text-muted-foreground/60 shrink-0"
              aria-hidden="true"
            />
            {crumb.isLast ? (
              <span
                className="text-foreground font-medium truncate max-w-[200px]"
                aria-current="page"
              >
                {crumbContent}
              </span>
            ) : (
              <Link
                href={crumb.href}
                className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
              >
                {crumbContent}
              </Link>
            )}
          </Fragment>
        )
      })}
    </nav>
  )
}
