'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'

import { EmptyState } from '@/components/shared/empty-state'
import { DomainIcon } from '@/components/shared/icon'
import { PageHeader } from '@/components/shared/page-header'
import { MarketplaceCard, type PrimaryCta } from '@/components/marketplace/marketplace-card'
import { MarketplaceFilterBar } from '@/components/marketplace/marketplace-filter-bar'
import { InstallWizard } from '@/components/marketplace/install-wizard'
import { UpdateStrategyDialog } from '@/components/marketplace/update-strategy-dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { useSession } from '@/lib/auth/session'
import { useMarketplaceItems } from '@/lib/hooks/use-marketplace'
import type {
  MarketplaceItem,
  MarketplaceListFilters,
  MarketplaceResourceType,
} from '@/lib/types/marketplace'

type Tab = 'all' | 'skills' | 'agents' | 'mcp' | 'installed'

const TABS: { value: Tab; labelKey: string; iconId: string; disabled?: boolean }[] = [
  { value: 'all', labelKey: 'tabs.all', iconId: 'marketplace' },
  { value: 'skills', labelKey: 'tabs.skills', iconId: 'skill' },
  { value: 'agents', labelKey: 'tabs.agents', iconId: 'agent', disabled: true },
  { value: 'mcp', labelKey: 'tabs.mcp', iconId: 'mcp', disabled: true },
  { value: 'installed', labelKey: 'tabs.installed', iconId: 'package' },
]

function resourceFilter(tab: Tab): MarketplaceResourceType | undefined {
  if (tab === 'skills') return 'skill'
  if (tab === 'agents') return 'agent'
  if (tab === 'mcp') return 'mcp'
  return undefined
}

export default function MarketplaceCatalogPage() {
  const t = useTranslations('marketplace.catalog')
  const { data: user } = useSession()
  const [tab, setTab] = useState<Tab>('skills')
  const [filters, setFilters] = useState<MarketplaceListFilters>({})
  const [installTarget, setInstallTarget] = useState<MarketplaceItem | null>(null)
  const [updateTarget, setUpdateTarget] = useState<MarketplaceItem | null>(null)

  const effectiveFilters = useMemo<MarketplaceListFilters>(() => {
    const next: MarketplaceListFilters = {
      ...filters,
      resource_type: resourceFilter(tab),
    }
    if (tab === 'installed') next.installed = true
    return next
  }, [filters, tab])

  const enabled = tab !== 'agents' && tab !== 'mcp'
  const { data: items, isLoading } = useMarketplaceItems(enabled ? effectiveFilters : undefined)

  function handleAction(item: MarketplaceItem, cta: PrimaryCta) {
    if (cta.kind === 'install' || cta.kind === 'setup') {
      setInstallTarget(item)
      return
    }
    if (cta.kind === 'update' || cta.kind === 'review_update') {
      setUpdateTarget(item)
      return
    }
    if (cta.kind === 'open' && item.installation.installed_resource_id) {
      window.location.href = `/skills?detailId=${item.installation.installed_resource_id}`
      return
    }
    window.location.href = `/marketplace/${item.id}`
  }

  return (
    <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
      <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-6 px-6 py-7 pb-20 md:px-8">
        <PageHeader
          title={t('title')}
          description={t('description')}
        />

        <div
          role="tablist"
          aria-label={t('categoriesLabel')}
          className="inline-flex w-fit max-w-full gap-1 overflow-x-auto rounded-xl border border-border bg-muted/60 p-1"
        >
          {TABS.map((tabOption) => {
            const isActive = tab === tabOption.value
            return (
              <button
                key={tabOption.value}
                type="button"
                role="tab"
                aria-selected={isActive}
                disabled={tabOption.disabled}
                onClick={() => setTab(tabOption.value)}
                className={cn(
                  'inline-flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3.5 text-sm transition-colors',
                  isActive
                    ? 'bg-background font-semibold text-foreground shadow-sm'
                    : 'font-medium text-muted-foreground hover:text-foreground',
                  tabOption.disabled && 'cursor-not-allowed opacity-50 hover:text-muted-foreground',
                )}
              >
                <DomainIcon iconId={tabOption.iconId} className="size-4 text-current" />
                {t(tabOption.labelKey)}
              </button>
            )
          })}
        </div>

        <div className="space-y-4">
          {enabled ? (
            <>
              <MarketplaceFilterBar
                filters={filters}
                onChange={setFilters}
                superUser={!!user?.is_super_user}
              />

              {isLoading ? (
                <CardGridSkeleton />
              ) : !items || items.length === 0 ? (
                <EmptyState
                  iconId="marketplace"
                  title={t('empty.title')}
                  description={t('empty.description')}
                />
              ) : (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {items.map((item) => (
                    <MarketplaceCard key={item.id} item={item} onAction={handleAction} />
                  ))}
                </div>
              )}
            </>
          ) : (
            <EmptyState
              iconId={tab === 'agents' ? 'agent' : 'mcp'}
              title={t('comingSoon.title')}
              description={
                tab === 'agents'
                  ? t('comingSoon.agents')
                  : t('comingSoon.mcp')
              }
            />
          )}
        </div>

        <InstallWizard
          item={installTarget}
          open={!!installTarget}
          onOpenChange={(open) => !open && setInstallTarget(null)}
        />
        <UpdateStrategyDialog
          item={updateTarget}
          open={!!updateTarget}
          onOpenChange={(open) => !open && setUpdateTarget(null)}
        />
      </div>
    </div>
  )
}

function CardGridSkeleton() {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-44 w-full rounded-xl" />
      ))}
    </div>
  )
}
