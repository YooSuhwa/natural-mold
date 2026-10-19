'use client'

import { Fragment } from 'react'
import { usePathname } from 'next/navigation'
import { ChevronRightIcon, HomeIcon } from 'lucide-react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { useAgent } from '@/lib/hooks/use-agents'
import { useConversationPages } from '@/lib/hooks/use-conversations'
import { useMarketplaceItem } from '@/lib/hooks/use-marketplace'
import { usePlatformTenant } from '@/lib/hooks/use-platform-tenants'
import { useSkill } from '@/lib/hooks/use-skills'

const ROUTE_LABELS: Record<string, string> = {
  tools: 'nav.tools',
  artifacts: 'sidebar.nav.artifacts',
  models: 'nav.models',
  schedules: 'nav.schedules',
  usage: 'nav.usage',
  settings: 'nav.settings',
  security: 'nav.security',
  appearance: 'nav.appearance',
  'agent-api': 'nav.agentApi',
  memory: 'appSettings.nav.memory',
  audit: 'appSettings.nav.audit',
  credentials: 'sidebar.nav.credentials',
  'mcp-servers': 'sidebar.nav.mcpServers',
  marketplace: 'sidebar.nav.marketplace',
  moderation: 'sidebar.nav.moderation',
  company: 'nav.company',
  organization: 'nav.organization',
  invite: 'nav.invite',
  traces: 'nav.traces',
  'marketplace-admin': 'nav.marketplaceAdmin',
  'admin-audit': 'nav.adminAudit',
  authz: 'nav.authzSync',
  tenants: 'nav.platformTenants',
  'system-credentials': 'nav.systemCredentials',
  'system-llm': 'nav.systemLlm',
  skills: 'nav.skills',
  // 스킬 스튜디오 탭 세그먼트 (Phase 2) — settings는 기존 라벨 재사용.
  evaluation: 'nav.skillEvaluation',
  versions: 'nav.skillVersions',
  source: 'nav.skillSource',
  builder: 'nav.skillBuilder',
  new: 'nav.createAgent',
  'visual-settings': 'nav.visualSettings',
  conversational: 'nav.conversational',
  manual: 'nav.manual',
  template: 'nav.template',
}

// Slugs whose label depends on the parent segment (`members` under company vs organization).
const SCOPED_LABELS: Record<string, string> = {
  'company/members': 'appSettings.nav.companyMembers',
  'company/policies': 'appSettings.nav.companyPolicies',
  'company/models': 'appSettings.nav.companyModels',
  'company/sso': 'appSettings.nav.companySso',
  'organization/members': 'appSettings.nav.organizationMembers',
  'organization/groups': 'appSettings.nav.organizationGroups',
  'organization/roles': 'appSettings.nav.organizationRoles',
  'organization/requests': 'appSettings.nav.organizationRequests',
  'organization/catalog': 'appSettings.nav.organizationCatalog',
  'admin/audit': 'nav.adminAudit',
  'conversations/new': 'nav.newConversation',
}

// Segments that never have their own page — always skip
// `admin` only groups operator pages (/settings/admin/authz); it has no page itself.
const SKIP_SEGMENTS = new Set(['agents', 'conversations', 'admin'])
// (no conditional skip — "new" always shown for context)

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

/** Id segments resolved to a name; a generic label stands in until (or unless) it loads. */
type IdKind = 'agent' | 'conversation' | 'marketplaceItem' | 'skill' | 'tenant'
const ID_PARENTS: Record<string, IdKind> = {
  agents: 'agent',
  conversations: 'conversation',
  marketplace: 'marketplaceItem',
  skills: 'skill',
  tenants: 'tenant',
}

function AgentName({ id, fallback }: { id: string; fallback: string }) {
  const { data: agent } = useAgent(id)
  return <>{agent?.name ?? fallback}</>
}

function ConversationTitle({
  agentId,
  conversationId,
  fallback,
}: {
  agentId: string
  conversationId: string
  fallback: string
}) {
  const { data: conversationPages } = useConversationPages(agentId, { limit: 30 })
  const conv = conversationPages?.pages
    .flatMap((page) => page.items)
    .find((c) => c.id === conversationId)
  return <>{conv?.title ?? fallback}</>
}

function SkillName({ id, fallback }: { id: string; fallback: string }) {
  // A 403/404 (no access) leaves `data` empty — never show the raw id.
  const { data: skill } = useSkill(id)
  return <>{skill?.name ?? fallback}</>
}

function MarketplaceItemName({ id, fallback }: { id: string; fallback: string }) {
  // detail page 에서 동일 query 가 이미 cache 되어 있어 추가 round-trip 없음.
  const { data: item } = useMarketplaceItem(id)
  return <>{item?.name ?? fallback}</>
}

function TenantName({ id, fallback }: { id: string; fallback: string }) {
  const { data: tenant } = usePlatformTenant(id)
  return <>{tenant?.name ?? fallback}</>
}

const FALLBACK_LABELS: Record<IdKind, string> = {
  agent: 'nav.agentDetail',
  conversation: 'nav.conversations',
  marketplaceItem: 'nav.marketplaceItem',
  skill: 'nav.skillDetail',
  tenant: 'nav.companyDetail',
}

interface Crumb {
  label: string
  href: string
  isLast: boolean
  idKind: IdKind | null
}

function CrumbContent({ crumb, agentId }: { crumb: Crumb; agentId: string | null }) {
  const t = useTranslations()
  if (!crumb.idKind) return <>{crumb.label}</>
  const id = crumb.label
  const fallback = t(FALLBACK_LABELS[crumb.idKind])
  switch (crumb.idKind) {
    case 'agent':
      return <AgentName id={id} fallback={fallback} />
    case 'conversation':
      return agentId ? (
        <ConversationTitle agentId={agentId} conversationId={id} fallback={fallback} />
      ) : (
        <>{fallback}</>
      )
    case 'marketplaceItem':
      return <MarketplaceItemName id={id} fallback={fallback} />
    case 'skill':
      return <SkillName id={id} fallback={fallback} />
    case 'tenant':
      return <TenantName id={id} fallback={fallback} />
  }
}

export function BreadcrumbNav() {
  const pathname = usePathname()
  const t = useTranslations()
  const ta = useTranslations('common.a11y')
  const segments = pathname.split('/').filter(Boolean)

  if (segments.length === 0) return null

  // Build crumbs, skipping segments that have no page; remember the agent id for
  // resolving conversation titles.
  let agentId: string | null = null
  const crumbs: Crumb[] = []

  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i]
    const parent = i > 0 ? segments[i - 1] : ''
    const href = '/' + segments.slice(0, i + 1).join('/')
    const isId = UUID_RE.test(segment)

    if (isId && parent === 'agents') agentId = segment

    if (SKIP_SEGMENTS.has(segment)) continue
    // 빌더 세션 uuid는 자체 라벨이 없다 — '빌더' 크럼(인덱스 링크)까지만 표시.
    if (isId && parent === 'builder') continue
    // An invitation token is a secret, not a label.
    if (parent === 'invite') continue

    const idKind = isId ? (ID_PARENTS[parent] ?? null) : null
    const key = SCOPED_LABELS[`${parent}/${segment}`] ?? (isId ? undefined : ROUTE_LABELS[segment])
    const label = key ? t(key) : segment
    // `/invite` has no page of its own: its crumb is the current page.
    const isLast = i === segments.length - 1 || segment === 'invite'
    crumbs.push({ label, href, isLast, idKind })
  }

  return (
    <nav aria-label={ta('breadcrumb')} className="flex items-center gap-1.5 text-sm min-w-0">
      <Link
        href="/"
        aria-label={t('sidebar.nav.home')}
        className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
      >
        <HomeIcon className="size-4" />
      </Link>

      {crumbs.map((crumb) => {
        const crumbContent = <CrumbContent crumb={crumb} agentId={agentId} />

        return (
          <Fragment key={crumb.href}>
            <ChevronRightIcon
              className="size-3.5 text-muted-foreground/60 shrink-0"
              aria-hidden="true"
            />
            {crumb.isLast ? (
              <span className="text-foreground font-medium truncate max-w-52" aria-current="page">
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
