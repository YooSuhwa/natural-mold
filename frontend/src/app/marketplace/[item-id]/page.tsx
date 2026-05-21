'use client'

import { use, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import { ChevronLeftIcon, SparklesIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/shared/empty-state'
import { ErrorState } from '@/components/shared/error-state'
import { PageHeader } from '@/components/shared/page-header'
import { CredentialBadge } from '@/components/marketplace/badges/credential-badge'
import { InstallationBadge } from '@/components/marketplace/badges/installation-badge'
import { OriginBadge } from '@/components/marketplace/badges/origin-badge'
import { PublicationBadge } from '@/components/marketplace/badges/publication-badge'
import { SupportBadge } from '@/components/marketplace/badges/support-badge'
import { InstallWizard } from '@/components/marketplace/install-wizard'
import { UpdateStrategyDialog } from '@/components/marketplace/update-strategy-dialog'
import { derivePrimaryCta } from '@/components/marketplace/marketplace-card'
import { useSession } from '@/lib/auth/session'
import { ApiError } from '@/lib/api/client'
import {
  useDisableItem,
  useMarketplaceItem,
  useMarketplaceVersions,
} from '@/lib/hooks/use-marketplace'
import type { MarketplaceItem } from '@/lib/types/marketplace'
import { formatMediumDate } from '@/lib/utils/format-relative-time'

interface PageProps {
  params: Promise<{ 'item-id': string }>
}

export default function MarketplaceItemDetailPage({ params }: PageProps) {
  const { 'item-id': itemId } = use(params)
  const router = useRouter()
  const { data: user } = useSession()
  const { data: item, isLoading, error } = useMarketplaceItem(itemId)
  const { data: versions } = useMarketplaceVersions(itemId)
  const disableItem = useDisableItem()
  const [installOpen, setInstallOpen] = useState(false)
  const [updateOpen, setUpdateOpen] = useState(false)

  if (isLoading) {
    return (
      <div className="flex flex-1 flex-col gap-4 overflow-auto p-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full rounded-xl" />
        <Skeleton className="h-48 w-full rounded-xl" />
      </div>
    )
  }

  if (error || !item) {
    const notFound =
      error instanceof ApiError &&
      (error.code === 'marketplace_item_not_found' || error.status === 404)
    return (
      <div className="flex flex-1 flex-col gap-4 overflow-auto p-6">
        <Link href="/marketplace" className="inline-flex items-center gap-1 text-sm text-primary-strong">
          <ChevronLeftIcon className="size-4" />
          Back to Marketplace
        </Link>
        <ErrorState
          title={notFound ? 'Item not found' : 'Failed to load item'}
          description="이 항목을 찾을 수 없거나 접근 권한이 없습니다."
        />
      </div>
    )
  }

  const isOwner = !!user && item.publication_summary.item_id === item.id && user.id === userIdFromOrigin(item)
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
    if (cta.kind === 'open' && item.installation.installed_resource_id) {
      router.push(`/skills?detailId=${item.installation.installed_resource_id}`)
    }
  }

  async function handleDisable() {
    if (!item) return
    try {
      await disableItem.mutateAsync(item.id)
      toast.success('Disabled')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to disable')
    }
  }

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <Link
        href="/marketplace"
        className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronLeftIcon className="size-4" />
        Back to Marketplace
      </Link>

      <PageHeader
        title={item.name}
        description={item.description ?? undefined}
        action={
          <div className="flex items-center gap-2">
            <Button
              variant={cta.variant}
              disabled={cta.disabled}
              onClick={handlePrimary}
            >
              {cta.label}
            </Button>
          </div>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <OriginBadge summary={item.origin_summary} />
        <PublicationBadge summary={item.publication_summary} />
        <CredentialBadge summary={item.credential_summary} />
        <SupportBadge profile={item.execution_profile} />
        <InstallationBadge summary={item.installation} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Versions</CardTitle>
          </CardHeader>
          <CardContent>
            {!versions || versions.length === 0 ? (
              <EmptyState
                icon={<SparklesIcon className="size-5" />}
                title="No versions published yet"
                description="Owners can publish a first version from the skill detail page."
              />
            ) : (
              <ul className="space-y-2 text-sm">
                {versions.map((v) => (
                  <li
                    key={v.id}
                    className="flex items-center justify-between rounded-md border border-border/60 px-3 py-2"
                  >
                    <span className="font-medium">v{v.version_label}</span>
                    <span className="text-xs text-muted-foreground">
                      {formatMediumDate(v.created_at)}
                      {v.source_commit ? ` · ${v.source_commit.slice(0, 7)}` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Execution profile</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm text-muted-foreground">
            {item.execution_profile ? (
              Object.entries(item.execution_profile).map(([key, value]) => (
                <p key={key}>
                  <span className="font-medium text-foreground">{key}</span>:{' '}
                  {Array.isArray(value) ? value.join(', ') : String(value)}
                </p>
              ))
            ) : (
              <p>No execution profile attached.</p>
            )}
          </CardContent>
        </Card>
      </div>

      {(isOwner || user?.is_super_user) ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">
              {user?.is_super_user ? 'Moderation actions' : 'Owner actions'}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={handleDisable} disabled={disableItem.isPending}>
              {disableItem.isPending ? 'Disabling…' : 'Disable item'}
            </Button>
            <span className="text-xs text-muted-foreground">
              Metadata edit / ACL / new version flows are part of the next slice.
            </span>
          </CardContent>
        </Card>
      ) : null}

      <InstallWizard item={item} open={installOpen} onOpenChange={setInstallOpen} />
      <UpdateStrategyDialog
        item={item}
        open={updateOpen}
        onOpenChange={setUpdateOpen}
      />
    </div>
  )
}

function userIdFromOrigin(item: MarketplaceItem): string | null {
  // origin_summary.source_user_id is set when current user is the owner who
  // imported it elsewhere; for ownership we can't determine without API.
  // Phase 1 keeps this minimal — owner actions guard relies on backend 403.
  return item.origin_summary?.source_user_id ?? null
}
