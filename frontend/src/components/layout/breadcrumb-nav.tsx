'use client'

import { Fragment } from 'react'
import { usePathname } from 'next/navigation'
import { ChevronRightIcon, HomeIcon } from 'lucide-react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { useQueryClient } from '@tanstack/react-query'

const ROUTE_LABELS: Record<string, string> = {
  tools: 'nav.tools',
  models: 'nav.models',
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
  const queryClient = useQueryClient()
  const agents = queryClient.getQueryData<{ id: string; name: string }[]>(['agents'])
  const agent = agents?.find((a) => a.id === id)
  return <>{agent?.name ?? `${id.slice(0, 8)}…`}</>
}

function ConversationTitle({
  agentId,
  conversationId,
}: {
  agentId: string
  conversationId: string
}) {
  const queryClient = useQueryClient()
  const conversations = queryClient.getQueryData<{ id: string; title?: string | null }[]>([
    'agents',
    agentId,
    'conversations',
  ])
  const conv = conversations?.find((c) => c.id === conversationId)
  return <>{conv?.title ?? `${conversationId.slice(0, 8)}…`}</>
}

export function BreadcrumbNav() {
  const pathname = usePathname()
  const t = useTranslations()
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

    let label = segment
    if (!isId && ROUTE_LABELS[segment]) {
      label = t(ROUTE_LABELS[segment])
    }

    crumbs.push({ label, href, isLast, isAgentId, isConvId })
  }

  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm min-w-0">
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
