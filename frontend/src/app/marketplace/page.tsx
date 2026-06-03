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

const TABS: { value: Tab; labelKey: string; disabled?: boolean }[] = [
  { value: 'all', labelKey: 'tabs.all' },
  { value: 'skills', labelKey: 'tabs.skills' },
  { value: 'agents', labelKey: 'tabs.agents', disabled: true },
  { value: 'mcp', labelKey: 'tabs.mcp', disabled: true },
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

  const enabled = tab !== 'agents' && tab !== 'mcp'
  const { data: itemsPage, isLoading } = useMarketplaceItemsPage(
    enabled ? { ...effectiveFilters, limit: 24, offset: 0 } : undefined,
    enabled,
  )
  const items = itemsPage?.items
  const countLabel =
    enabled && !isLoading && items
      ? t('count', { count: itemsPage?.total ?? items.length })
      : undefined
  const tabs = TABS.map((tabOption) => ({
    value: tabOption.value,
    label: t(tabOption.labelKey),
    countLabel,
    disabled: tabOption.disabled,
  }))

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
    <ResourcePage title={t('title')} description={t('description')}>
      <ResourcePanel>
        <ResourcePanel.Toolbar>
          <CountedLineTabs
            ariaLabel={t('categoriesLabel')}
            value={tab}
            tabs={tabs}
            onValueChange={(next) => setTab(next as Tab)}
          />

          {enabled ? (
            <MarketplaceFilterBar
              filters={filters}
              onChange={setFilters}
              superUser={!!user?.is_super_user}
            />
          ) : null}
        </ResourcePanel.Toolbar>

        <ResourcePanel.Body className="bg-background/30">
          {enabled ? (
            isLoading ? (
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
            )
          ) : (
            <EmptyState
              icon={<SparklesIcon className="size-6" />}
              title={t('comingSoon.title')}
              description={tab === 'agents' ? t('comingSoon.agents') : t('comingSoon.mcp')}
              className="bg-card/50"
            />
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
        <Skeleton key={i} className="h-44 w-full rounded-xl" />
      ))}
    </ResourceGrid>
  )
}
