'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import Image from 'next/image'
import {
  PlusIcon,
  SparklesIcon,
  MessageSquareIcon,
  LayoutTemplateIcon,
  PenLineIcon,
  SearchIcon,
  StarIcon,
  ArrowUpDownIcon,
  ChevronRightIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useAgents } from '@/lib/hooks/use-agents'
import { useUsageSummary } from '@/lib/hooks/use-usage'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { SearchInput } from '@/components/shared/search-input'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu'
import { AgentCard } from '@/components/agent/agent-card'
import { AgentCardSkeleton } from '@/components/agent/agent-card-skeleton'
import { EmptyState } from '@/components/shared/empty-state'
import type { Agent } from '@/lib/types'

type SortKey = 'latest' | 'name' | 'favorite'

export default function DashboardPage() {
  const t = useTranslations('dashboard')
  const { data: agents, isLoading: agentsLoading } = useAgents()
  const { data: usage } = useUsageSummary()
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState<SortKey>('latest')
  const [showFavoritesOnly, setShowFavoritesOnly] = useState(false)

  const quickActions = [
    {
      label: t('quickAction.conversational.label'),
      description: t('quickAction.conversational.description'),
      href: '/agents/new',
      icon: MessageSquareIcon,
      iconBg: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-300',
      cardBg:
        'bg-gradient-to-br from-emerald-50 via-emerald-50/30 to-white dark:from-emerald-950/40 dark:via-emerald-950/10 dark:to-card',
    },
    {
      label: t('quickAction.manual.label'),
      description: t('quickAction.manual.description'),
      href: '/agents/new/manual',
      icon: PenLineIcon,
      iconBg: 'bg-violet-100 text-violet-600 dark:bg-violet-500/20 dark:text-violet-300',
      cardBg:
        'bg-gradient-to-br from-violet-50 via-violet-50/30 to-white dark:from-violet-950/40 dark:via-violet-950/10 dark:to-card',
    },
    {
      label: t('quickAction.template.label'),
      description: t('quickAction.template.description'),
      href: '/agents/new/template',
      icon: LayoutTemplateIcon,
      iconBg: 'bg-sky-100 text-sky-600 dark:bg-sky-500/20 dark:text-sky-300',
      cardBg:
        'bg-gradient-to-br from-sky-50 via-sky-50/30 to-white dark:from-sky-950/40 dark:via-sky-950/10 dark:to-card',
    },
  ]

  const SORT_LABELS: Record<SortKey, string> = {
    latest: t('sort.latest'),
    name: t('sort.name'),
    favorite: t('sort.favorite'),
  }

  const filteredAgents = useMemo(() => {
    if (!agents) return []
    let result: Agent[] = agents

    // Search filter
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      result = result.filter(
        (a) =>
          a.name.toLowerCase().includes(q) ||
          (a.description && a.description.toLowerCase().includes(q)),
      )
    }

    // Favorites filter
    if (showFavoritesOnly) {
      result = result.filter((a) => a.is_favorite)
    }

    // Sort
    result = [...result].sort((a, b) => {
      if (sortBy === 'name') return a.name.localeCompare(b.name, 'ko')
      if (sortBy === 'favorite') {
        if (a.is_favorite !== b.is_favorite) return a.is_favorite ? -1 : 1
      }
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    })

    return result
  }, [agents, search, sortBy, showFavoritesOnly])

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 overflow-auto p-6">
      <section className="relative overflow-hidden rounded-3xl border border-emerald-100/60 bg-gradient-to-br from-emerald-50/60 via-background to-background p-6 sm:p-8 dark:border-emerald-500/10 dark:from-emerald-950/20">
        <div className="flex items-center justify-between gap-4">
          <div className="flex-1 space-y-2">
            <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{t('greeting')}</h1>
            <p className="text-sm text-muted-foreground sm:text-base">{t('subtitle')}</p>
          </div>
          <div className="relative hidden aspect-[738/628] shrink-0 sm:block sm:w-32 md:w-40 lg:w-44">
            <Image
              src="/dashboard-mascot.webp"
              alt=""
              fill
              preload
              sizes="(min-width: 1024px) 11rem, (min-width: 768px) 10rem, 8rem"
              className="object-contain"
            />
          </div>
          <Link href="/agents/new" className="shrink-0">
            <Button variant="emeraldStrong">
              <PlusIcon className="size-4" data-icon="inline-start" />
              {t('newAgent')}
            </Button>
          </Link>
        </div>
      </section>

      <div className="grid gap-4 sm:grid-cols-3">
        {quickActions.map((action) => (
          <Link key={action.href} href={action.href} className="group">
            <Card
              className={`cursor-pointer transition-all hover:shadow-md hover:ring-foreground/15 ${action.cardBg}`}
            >
              <CardContent className="flex items-center gap-4 p-4">
                <div
                  className={`flex size-11 shrink-0 items-center justify-center rounded-xl ${action.iconBg}`}
                >
                  <action.icon className="size-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold tracking-tight transition-colors group-hover:text-foreground">
                    {action.label}
                  </p>
                  <p className="line-clamp-1 text-xs text-muted-foreground">{action.description}</p>
                </div>
                <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-white/80 ring-1 ring-foreground/5 shadow-sm transition-all group-hover:translate-x-0.5 group-hover:shadow dark:bg-background/60">
                  <ChevronRightIcon className="size-3.5 text-muted-foreground transition-colors group-hover:text-foreground" />
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>

      {agentsLoading ? (
        <div>
          <h2 className="mb-4 text-lg font-semibold tracking-tight">{t('myAgents')}</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <AgentCardSkeleton key={i} />
            ))}
          </div>
        </div>
      ) : agents && agents.length > 0 ? (
        <div>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <h2 className="text-lg font-semibold tracking-tight">{t('myAgents')}</h2>
            <div className="ml-auto flex items-center gap-2">
              <SearchInput
                placeholder={t('searchPlaceholder')}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="h-9 w-48 text-sm"
              />

              <Button
                variant={showFavoritesOnly ? 'default' : 'outline'}
                size="sm"
                onClick={() => setShowFavoritesOnly(!showFavoritesOnly)}
                className="h-9"
              >
                <StarIcon className={`size-4 ${showFavoritesOnly ? 'fill-current' : ''}`} />
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
                <AgentCard key={agent.id} agent={agent} />
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed p-8 text-center">
              <SearchIcon className="mb-2 size-6 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">{t('noSearchResults')}</p>
            </div>
          )}
        </div>
      ) : (
        <EmptyState
          icon={<SparklesIcon className="size-6" />}
          title={t('empty.title')}
          description={t('empty.description')}
        />
      )}

      {usage && usage.total_tokens > 0 && (
        <div className="rounded-xl border bg-muted/30 p-4">
          <h2 className="mb-2 text-sm font-medium text-muted-foreground">
            {t('usageSummary.title')}
          </h2>
          <div className="flex items-center gap-6 text-sm">
            <div>
              <span className="text-muted-foreground">{t('usageSummary.totalTokens')}</span>
              <span className="font-medium">{usage.total_tokens.toLocaleString()}</span>
            </div>
            <div>
              <span className="text-muted-foreground">{t('usageSummary.estimatedCost')}</span>
              <span className="font-medium">${usage.estimated_cost_usd.toFixed(2)}</span>
            </div>
          </div>
        </div>
      )}

      <p className="pb-2 text-center text-xs text-muted-foreground">{t('tip')}</p>
    </div>
  )
}
