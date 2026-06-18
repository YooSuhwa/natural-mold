'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import Image from 'next/image'
import {
  MessageSquareIcon,
  LayoutTemplateIcon,
  PenLineIcon,
  SearchIcon,
  StarIcon,
  ArrowUpDownIcon,
  ChevronRightIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useAgentSummaries } from '@/lib/hooks/use-agents'
import { useSession } from '@/lib/auth/session'
import { displayUserName } from '@/components/auth/UserAvatar'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { SearchInput } from '@/components/shared/search-input'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu'
import { AgentCard } from '@/components/agent/agent-card'
import { AgentCardSkeleton } from '@/components/agent/agent-card-skeleton'
import { PublishWizard } from '@/components/marketplace/publish-wizard'
import { EmptyState } from '@/components/shared/empty-state'
import { cn } from '@/lib/utils'
import type { AgentSummary } from '@/lib/types'

type SortKey = 'latest' | 'name' | 'favorite'

type GreetingKey =
  | 'greetingMorning'
  | 'greetingAfternoon'
  | 'greetingEvening'
  | 'greetingNight'
  | 'greetingLate'

function pickGreetingKey(date: Date = new Date()): GreetingKey {
  const h = date.getHours()
  if (h < 5) return 'greetingNight'
  if (h < 12) return 'greetingMorning'
  if (h < 18) return 'greetingAfternoon'
  if (h < 22) return 'greetingEvening'
  return 'greetingLate'
}

export default function DashboardPage() {
  const t = useTranslations('dashboard')
  const { data: agents, isLoading: agentsLoading } = useAgentSummaries()
  const { data: user } = useSession()
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState<SortKey>('latest')
  const [showFavoritesOnly, setShowFavoritesOnly] = useState(false)
  const [publishAgent, setPublishAgent] = useState<AgentSummary | null>(null)

  const greetingKey = pickGreetingKey()
  const userName = displayUserName(user) || t('userFallback')
  const agentCount = agents?.length ?? 0

  const SORT_LABELS: Record<SortKey, string> = {
    latest: t('sort.latest'),
    name: t('sort.name'),
    favorite: t('sort.favorite'),
  }

  const filteredAgents = useMemo(() => {
    if (!agents) return []
    let result: AgentSummary[] = agents

    if (search.trim()) {
      const q = search.trim().toLowerCase()
      result = result.filter(
        (a) =>
          a.name.toLowerCase().includes(q) ||
          (a.description && a.description.toLowerCase().includes(q)),
      )
    }

    if (showFavoritesOnly) {
      result = result.filter((a) => a.is_favorite)
    }

    result = [...result].sort((a, b) => {
      if (sortBy === 'name') return a.name.localeCompare(b.name, 'ko')
      if (sortBy === 'favorite') {
        if (a.is_favorite !== b.is_favorite) return a.is_favorite ? -1 : 1
      }
      const aTime = new Date(a.last_used_at ?? a.updated_at).getTime()
      const bTime = new Date(b.last_used_at ?? b.updated_at).getTime()
      return bTime - aTime
    })

    return result
  }, [agents, search, sortBy, showFavoritesOnly])

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 overflow-hidden p-6">
      {/* Hero */}
      <section className="moldy-dashboard-hero relative shrink-0 overflow-hidden p-6 sm:p-8">
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0 flex-1 space-y-1.5">
            <p className="text-sm font-medium text-muted-foreground">{t(greetingKey)},</p>
            <h1 className="text-2xl font-bold sm:text-3xl">
              {t('greetingName', { name: userName })}
            </h1>
            <p className="pt-1 text-sm text-muted-foreground sm:text-base">
              {t('subtitleWithCount', { count: agentCount })}
            </p>
          </div>
          <div className="relative hidden aspect-[738/628] shrink-0 sm:block sm:w-32 md:w-40 lg:w-44">
            <Image
              src="/dashboard-mascot.webp"
              alt=""
              fill
              priority
              sizes="(min-width: 1024px) 11rem, (min-width: 768px) 10rem, 8rem"
              className="object-contain"
            />
          </div>
        </div>
      </section>

      {/* Quick actions — 1.4fr + 1fr asymmetric grid */}
      <div className="grid shrink-0 grid-cols-1 gap-4 md:grid-cols-[1.4fr_1fr]">
        {/* Primary: 대화로 만들기 */}
        <Link href="/agents/new" className="moldy-card-link group">
          <Card className={cn('moldy-dashboard-action-primary h-full min-h-40 gap-3 p-1.5 ring-0')}>
            <CardContent className="flex h-full flex-col gap-3 p-5">
              <div className="flex items-center gap-3">
                <div className="moldy-dashboard-action-icon moldy-status-success flex size-11 shrink-0 items-center justify-center">
                  <MessageSquareIcon className="size-5" />
                </div>
                <div className="flex items-center gap-2">
                  <p className="text-base font-semibold">{t('quickAction.conversational.label')}</p>
                  <Badge className="moldy-dashboard-action-badge moldy-ui-micro tracking-wide hover:opacity-95">
                    {t('recommended')}
                  </Badge>
                </div>
              </div>
              <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
                {t('quickAction.conversational.description')}
              </p>
              <div className="moldy-status-success moldy-status-text mt-auto flex items-center gap-1 text-sm font-semibold transition-transform group-hover:translate-x-0.5">
                {t('startCta')}
                <ChevronRightIcon className="size-4" />
              </div>
            </CardContent>
          </Card>
        </Link>

        {/* Secondary stack: 직접 / 템플릿 */}
        <div className="grid grid-rows-2 gap-4">
          <SecondaryActionCard
            href="/agents/new/manual"
            icon={<PenLineIcon className="size-5" />}
            label={t('quickAction.manual.label')}
            description={t('quickAction.manual.description')}
            tone="violet"
          />
          <SecondaryActionCard
            href="/agents/new/template"
            icon={<LayoutTemplateIcon className="size-5" />}
            label={t('quickAction.template.label')}
            description={t('quickAction.template.description')}
            tone="sky"
          />
        </div>
      </div>

      {/* hero/quickActions는 fixed, 카드 그리드만 자체 스크롤. px-1은 카드 ring/border 잘림 방지 여백. */}
      <div className="scrollbar-hide flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto px-1 pb-16">
        {agentsLoading ? (
          <div>
            <h2 className="mb-4 text-lg font-semibold">{t('myAgents')}</h2>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <AgentCardSkeleton key={i} />
              ))}
            </div>
          </div>
        ) : agents && agents.length > 0 ? (
          <div>
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-semibold">
                {t('myAgents')}
                <span className="ml-2 text-sm font-medium text-muted-foreground">
                  {filteredAgents.length}
                </span>
              </h2>
              <div className="ml-auto flex items-center gap-2">
                <SearchInput
                  placeholder={t('searchPlaceholder')}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="h-9 w-48 text-sm"
                />

                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowFavoritesOnly(!showFavoritesOnly)}
                  aria-pressed={showFavoritesOnly}
                  className={cn(
                    'h-9',
                    showFavoritesOnly && 'moldy-status-surface moldy-status-warn hover:opacity-90',
                  )}
                >
                  <StarIcon className={cn('size-4', showFavoritesOnly && 'fill-current')} />
                </Button>

                <DropdownMenu>
                  <DropdownMenuTrigger
                    render={<Button variant="outline" size="sm" className="h-9 gap-1.5" />}
                  >
                    <ArrowUpDownIcon className="size-3.5" />
                    <span className="text-xs">{SORT_LABELS[sortBy]}</span>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => setSortBy('latest')}>
                      {t('sort.latest')}
                      {sortBy === 'latest' && <span className="ml-auto text-xs">✓</span>}
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setSortBy('name')}>
                      {t('sort.name')}
                      {sortBy === 'name' && <span className="ml-auto text-xs">✓</span>}
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setSortBy('favorite')}>
                      {t('sort.favoriteFirst')}
                      {sortBy === 'favorite' && <span className="ml-auto text-xs">✓</span>}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>

            {filteredAgents.length > 0 ? (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {filteredAgents.map((agent) => (
                  <AgentCard
                    key={agent.id}
                    agent={agent}
                    onPublish={(selected) => setPublishAgent(selected as AgentSummary)}
                  />
                ))}
              </div>
            ) : (
              <EmptyState
                className="p-8"
                icon={<SearchIcon className="size-6 text-muted-foreground" />}
                title={t('noSearchResults')}
              />
            )}
          </div>
        ) : (
          <EmptyState
            className="border-solid bg-card p-8"
            iconId="agent"
            title={t('empty.title')}
            description={t('empty.description')}
          />
        )}
      </div>
      <PublishWizard
        resource={
          publishAgent
            ? {
                id: publishAgent.id,
                resourceType: 'agent',
                name: publishAgent.name,
                description: publishAgent.description,
                iconId: 'agent',
                detailLabel: publishAgent.name,
                detailSubhead: publishAgent.model_display_name,
              }
            : null
        }
        open={!!publishAgent}
        onOpenChange={(open) => !open && setPublishAgent(null)}
      />
    </div>
  )
}

type SecondaryTone = 'violet' | 'sky'

const SECONDARY_TONE: Record<SecondaryTone, { iconBg: string }> = {
  violet: {
    iconBg: 'moldy-status-accent',
  },
  sky: {
    iconBg: 'moldy-status-info',
  },
}

interface SecondaryActionCardProps {
  href: string
  icon: React.ReactNode
  label: string
  description: string
  tone: SecondaryTone
}

function SecondaryActionCard({ href, icon, label, description, tone }: SecondaryActionCardProps) {
  const { iconBg } = SECONDARY_TONE[tone]
  return (
    <Link href={href} className="moldy-card-link group">
      <Card className={cn('moldy-dashboard-action h-full gap-0 py-0')}>
        <CardContent className="flex h-full items-center gap-3 p-4">
          <div
            className={cn(
              'moldy-dashboard-action-icon flex size-10 shrink-0 items-center justify-center',
              iconBg,
            )}
          >
            {icon}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">{label}</p>
            <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{description}</p>
          </div>
          <span className="moldy-dashboard-arrow">
            <ChevronRightIcon className="size-3.5" />
          </span>
        </CardContent>
      </Card>
    </Link>
  )
}
