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
import { cn } from '@/lib/utils'
import { useSession } from '@/lib/auth/session'
import { useMarketplaceItems } from '@/lib/hooks/use-marketplace'
import type {
  MarketplaceItem,
  MarketplaceListFilters,
  MarketplaceResourceType,
} from '@/lib/types/marketplace'

type Tab = 'all' | 'skills' | 'agents' | 'mcp' | 'installed'

const TABS: { value: Tab; label: string; disabled?: boolean }[] = [
  { value: 'all', label: '전체' },
  { value: 'skills', label: '스킬' },
  { value: 'agents', label: '에이전트 (Phase 3)', disabled: true },
  { value: 'mcp', label: 'MCP (Phase 2)', disabled: true },
  { value: 'installed', label: '설치됨' },
]

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
          title="마켓플레이스"
          description="공유된 스킬, 에이전트, MCP 서버를 발견하고 설치하세요."
        />

        <div
          role="tablist"
          aria-label="마켓플레이스 카테고리"
          className="inline-flex w-fit max-w-full gap-1 overflow-x-auto rounded-xl border border-border bg-muted/60 p-1"
        >
          {TABS.map((t) => {
            const isActive = tab === t.value
            return (
              <button
                key={t.value}
                type="button"
                role="tab"
                aria-selected={isActive}
                disabled={t.disabled}
                onClick={() => setTab(t.value)}
                className={cn(
                  'inline-flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3.5 text-sm transition-colors',
                  isActive
                    ? 'bg-background font-semibold text-foreground shadow-sm'
                    : 'font-medium text-muted-foreground hover:text-foreground',
                  t.disabled && 'cursor-not-allowed opacity-50 hover:text-muted-foreground',
                )}
              >
                {t.label}
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
                  icon={<SparklesIcon className="size-6" />}
                  title="조건에 맞는 항목이 없어요"
                  description="필터를 지우거나 k-skill 카탈로그를 동기화해 보세요 (관리자)."
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
              icon={<SparklesIcon className="size-6" />}
              title="곧 만나요"
              description={
                tab === 'agents'
                  ? '에이전트 마켓플레이스는 Phase 3에 공개될 예정이에요.'
                  : 'MCP 마켓플레이스는 Phase 2에 공개될 예정이에요.'
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
