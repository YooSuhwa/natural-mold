'use client'

import { useMemo, useState } from 'react'
import { SparklesIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { EmptyState } from '@/components/shared/empty-state'
import {
  CountedLineTabs,
  ResourceGrid,
  ResourcePage,
  ResourcePanel,
} from '@/components/shared/resource-layout'
import { MarketplaceCard, type PrimaryCta } from '@/components/marketplace/marketplace-card'
import { MarketplaceFilterBar } from '@/components/marketplace/marketplace-filter-bar'
import { InstallWizard } from '@/components/marketplace/install-wizard'
import { UpdateStrategyDialog } from '@/components/marketplace/update-strategy-dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { useSession } from '@/lib/auth/session'
import { useMarketplaceItemsPage } from '@/lib/hooks/use-marketplace'
import type {
  MarketplaceItem,
  MarketplaceListFilters,
  MarketplaceResourceType,
} from '@/lib/types/marketplace'

type Tab = 'all' | 'skills' | 'agents' | 'mcp' | 'installed'

const TABS: { value: Tab; labelKey: string }[] = [
  { value: 'all', labelKey: 'tabs.all' },
  { value: 'skills', labelKey: 'tabs.skills' },
  { value: 'agents', labelKey: 'tabs.agents' },
  { value: 'mcp', labelKey: 'tabs.mcp' },
  { value: 'installed', labelKey: 'tabs.installed' },
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

  const { data: itemsPage, isLoading } = useMarketplaceItemsPage({
    ...effectiveFilters,
    limit: 24,
    offset: 0,
  })
  const items = itemsPage?.items
  const countLabel =
    !isLoading && items ? t('count', { count: itemsPage?.total ?? items.length }) : undefined
  const tabs = TABS.map((tabOption) => ({
    value: tabOption.value,
    label: t(tabOption.labelKey),
    countLabel,
  }))

  function handleAction(item: MarketplaceItem, cta: PrimaryCta) {
    if (cta.kind === 'install' || cta.kind === 'setup') {
      setInstallTarget(item)
      return
    }
    if (cta.kind === 'update' || cta.kind === 'review_update') {
      setUpdateTarget(item)
    }
  }

  return (
    <ResourcePage title={t('title')} description={t('description')}>
      <ResourcePanel>
        <ResourcePanel.Toolbar>
          <CountedLineTabs
            ariaLabel={t('categoriesLabel')}
            value={tab}
            tabs={tabs}
            onValueChange={(next) => setTab(next as Tab)}
          />

          <MarketplaceFilterBar
            filters={filters}
            onChange={setFilters}
            superUser={!!user?.is_super_user}
          />
        </ResourcePanel.Toolbar>

        <ResourcePanel.Body className="bg-background/30">
          {isLoading ? (
            <CardGridSkeleton />
          ) : !items || items.length === 0 ? (
            <EmptyState
              icon={<SparklesIcon className="size-6" />}
              title={t('empty.title')}
              description={t('empty.description')}
              className="bg-card/50"
            />
          ) : (
            <ResourceGrid minColumnWidth={300}>
              {items.map((item) => (
                <MarketplaceCard key={item.id} item={item} onAction={handleAction} />
              ))}
            </ResourceGrid>
          )}
        </ResourcePanel.Body>
      </ResourcePanel>

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
    </ResourcePage>
  )
}

function CardGridSkeleton() {
  return (
    <ResourceGrid minColumnWidth={300}>
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="moldy-skeleton-card h-48 w-full" />
      ))}
    </ResourceGrid>
  )
}
