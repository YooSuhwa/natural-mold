'use client'

import { useMemo, useState } from 'react'
import { SparklesIcon } from 'lucide-react'

import { EmptyState } from '@/components/shared/empty-state'
import { PageHeader } from '@/components/shared/page-header'
import { MarketplaceCard, type PrimaryCta } from '@/components/marketplace/marketplace-card'
import { MarketplaceFilterBar } from '@/components/marketplace/marketplace-filter-bar'
import { InstallWizard } from '@/components/marketplace/install-wizard'
import { UpdateStrategyDialog } from '@/components/marketplace/update-strategy-dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useSession } from '@/lib/auth/session'
import { useMarketplaceItems } from '@/lib/hooks/use-marketplace'
import type {
  MarketplaceItem,
  MarketplaceListFilters,
  MarketplaceResourceType,
} from '@/lib/types/marketplace'

type Tab = 'all' | 'skills' | 'agents' | 'mcp' | 'installed'

function resourceFilter(tab: Tab): MarketplaceResourceType | undefined {
  if (tab === 'skills') return 'skill'
  if (tab === 'agents') return 'agent'
  if (tab === 'mcp') return 'mcp'
  return undefined
}

export default function MarketplaceCatalogPage() {
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
  const { data: items, isLoading } = useMarketplaceItems(
    enabled ? effectiveFilters : undefined,
  )

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
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader
        title="Marketplace"
        description="Discover and install shared skills, agents, and MCP servers."
      />

      <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
        <TabsList>
          <TabsTrigger value="all">All</TabsTrigger>
          <TabsTrigger value="skills">Skills</TabsTrigger>
          <TabsTrigger value="agents" disabled>
            Agents (Phase 3)
          </TabsTrigger>
          <TabsTrigger value="mcp" disabled>
            MCP (Phase 2)
          </TabsTrigger>
          <TabsTrigger value="installed">Installed</TabsTrigger>
        </TabsList>

        <TabsContent value={tab} className="mt-4 space-y-4">
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
                  icon={<SparklesIcon className="size-6" />}
                  title="No items match your filters"
                  description="Try clearing filters or sync the k-skill catalog (admin)."
                />
              ) : (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {items.map((item) => (
                    <MarketplaceCard
                      key={item.id}
                      item={item}
                      onAction={handleAction}
                    />
                  ))}
                </div>
              )}
            </>
          ) : (
            <EmptyState
              icon={<SparklesIcon className="size-6" />}
              title="Coming soon"
              description={
                tab === 'agents'
                  ? 'Agent marketplace ships in Phase 3.'
                  : 'MCP marketplace ships in Phase 2.'
              }
            />
          )}
        </TabsContent>
      </Tabs>

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
