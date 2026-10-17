'use client'

import { use, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { ChevronLeftIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { PageHeader } from '@/components/shared/page-header'
import { DomainIconTile, getDomainIconIdForResource } from '@/components/shared/icon'
import { CredentialBadge } from '@/components/marketplace/badges/credential-badge'
import { InstallationBadge } from '@/components/marketplace/badges/installation-badge'
import { OriginBadge } from '@/components/marketplace/badges/origin-badge'
import { PublicationBadge } from '@/components/marketplace/badges/publication-badge'
import { SupportBadge } from '@/components/marketplace/badges/support-badge'
import { InstallWizard } from '@/components/marketplace/install-wizard'
import { MarketplaceShareDialog } from '@/components/marketplace/marketplace-share-dialog'
import { UpdateStrategyDialog } from '@/components/marketplace/update-strategy-dialog'
import { derivePrimaryCta, getPrimaryCtaHref } from '@/components/marketplace/marketplace-card'
import { marketplaceItemAccess } from '@/components/marketplace/marketplace-access'
import { useSession } from '@/lib/auth/session'
import { useMarketplaceItem, useMarketplaceVersions } from '@/lib/hooks/use-marketplace'
import { MarketplaceDetailCards } from './_components/marketplace-detail-cards'
import { MarketplaceDetailError } from './_components/marketplace-detail-error'
import { MarketplaceManagementCard } from './_components/marketplace-management-card'

interface PageProps {
  params: Promise<{ 'item-id': string }>
}

export default function MarketplaceItemDetailPage({ params }: PageProps) {
  const t = useTranslations('marketplace.detail')
  const tCard = useTranslations('marketplace.card')
  const { 'item-id': itemId } = use(params)
  const router = useRouter()
  const { data: user } = useSession()
  const { data: item, isLoading, error } = useMarketplaceItem(itemId)
  const { data: versions } = useMarketplaceVersions(itemId)
  const [shareOpen, setShareOpen] = useState(false)
  const [installOpen, setInstallOpen] = useState(false)
  const [updateOpen, setUpdateOpen] = useState(false)

  if (isLoading) {
    return (
      <div className="moldy-app-surface flex flex-1 flex-col overflow-auto">
        <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-4 px-6 py-7 pb-20 md:px-8">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="moldy-skeleton-card h-32 w-full" />
          <Skeleton className="moldy-skeleton-card h-48 w-full" />
        </div>
      </div>
    )
  }

  if (error || !item) {
    return <MarketplaceDetailError error={error} />
  }

  // Owner 판별: 백엔드가 ``publication_summary.item_id`` 를 owner+link 동시일
  // 때만 채워주므로 (service.py `_project_item`), 이 한 줄로 충분하다.
  // origin_summary.source_user_id 는 다른 user 가 공유해준 케이스에만 set 되어
  // 본인 publish 에서는 null — 이전 비교는 항상 false 였다.
  const { canManage, canInstall } = marketplaceItemAccess(item, user?.id)
  const cta = derivePrimaryCta(item)

  function handlePrimary() {
    if (!item) return
    if (cta.kind === 'install' || cta.kind === 'setup') {
      setInstallOpen(true)
      return
    }
    if (cta.kind === 'update' || cta.kind === 'review_update') {
      setUpdateOpen(true)
      return
    }
    const href = getPrimaryCtaHref(item, cta)
    if (href) {
      router.push(href)
    }
  }

  return (
    <div className="moldy-app-surface flex flex-1 flex-col overflow-auto">
      <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-6 py-7 pb-20 md:px-8">
        <Link
          href="/marketplace"
          className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ChevronLeftIcon className="size-4" />
          {t('back')}
        </Link>

        <div className="flex items-start gap-3">
          <DomainIconTile
            iconId={item.icon_id ?? getDomainIconIdForResource(item.resource_type)}
            className="mt-0.5 size-11"
            iconClassName="size-6"
          />
          <PageHeader
            className="flex-1"
            title={item.name}
            description={item.description ?? undefined}
            action={
              <div className="flex items-center gap-2">
                {canInstall ? (
                  <Button variant={cta.variant} disabled={cta.disabled} onClick={handlePrimary}>
                    {tCard(`cta.${cta.kind}`)}
                  </Button>
                ) : null}
              </div>
            }
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <OriginBadge summary={item.origin_summary} />
          <PublicationBadge summary={item.publication_summary} />
          <CredentialBadge summary={item.credential_summary} />
          <SupportBadge profile={item.execution_profile} />
          <InstallationBadge summary={item.installation} />
        </div>

        <MarketplaceDetailCards item={item} versions={versions} />

        {canManage || user?.is_super_user ? (
          <MarketplaceManagementCard
            item={item}
            canManage={canManage}
            isSuperUser={!!user?.is_super_user}
            onManageAcl={() => setShareOpen(true)}
          />
        ) : null}

        <InstallWizard item={item} open={installOpen} onOpenChange={setInstallOpen} />
        <MarketplaceShareDialog item={item} open={shareOpen} onOpenChange={setShareOpen} />
        <UpdateStrategyDialog item={item} open={updateOpen} onOpenChange={setUpdateOpen} />
      </div>
    </div>
  )
}
