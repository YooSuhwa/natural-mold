'use client'

import { use, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { ChevronLeftIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/shared/empty-state'
import { ErrorState } from '@/components/shared/error-state'
import { PageHeader } from '@/components/shared/page-header'
import { DomainIconTile, getDomainIconIdForResource } from '@/components/shared/icon'
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
  useEnableItem,
  useMarketplaceItem,
  useMarketplaceVersions,
  usePatchMarketplaceItem,
  useRemoveItemACL,
} from '@/lib/hooks/use-marketplace'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { MarketplaceItemPatchBody, MarketplaceVisibility } from '@/lib/types/marketplace'
import { formatMediumDate } from '@/lib/utils/format-relative-time'

function shortHash(value?: string | null): string | null {
  if (!value) return null
  return value.slice(0, 7)
}

function formatExecutionProfileValue(
  key: string,
  value: unknown,
  t: (key: string) => string,
): string {
  if (key === 'support_level' && typeof value === 'string') {
    return t(`executionProfile.supportLevel.${value}`)
  }
  if (key === 'requires_network' && typeof value === 'boolean') {
    return value
      ? t('executionProfile.boolean.required')
      : t('executionProfile.boolean.notRequired')
  }
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(', ') : t('executionProfile.none')
  }
  if (value == null || value === '') return '—'
  return String(value)
}

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
  const disableItem = useDisableItem()
  const enableItem = useEnableItem()
  const patchItem = usePatchMarketplaceItem(itemId)
  const removeACL = useRemoveItemACL(itemId)
  const [installOpen, setInstallOpen] = useState(false)
  const [updateOpen, setUpdateOpen] = useState(false)

  if (isLoading) {
    return (
      <div className="moldy-app-surface flex flex-1 flex-col overflow-auto">
        <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-4 px-6 py-7 pb-20 md:px-8">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-32 w-full rounded-xl" />
          <Skeleton className="h-48 w-full rounded-xl" />
        </div>
      </div>
    )
  }

  if (error || !item) {
    const notFound =
      error instanceof ApiError &&
      (error.code === 'marketplace_item_not_found' || error.status === 404)
    return (
      <div className="moldy-app-surface flex flex-1 flex-col overflow-auto">
        <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-4 px-6 py-7 pb-20 md:px-8">
          <Link
            href="/marketplace"
            className="inline-flex items-center gap-1 text-sm text-primary-strong"
          >
            <ChevronLeftIcon className="size-4" />
            {t('back')}
          </Link>
          <ErrorState
            title={notFound ? t('error.notFound') : t('error.loadFailed')}
            description={t('error.description')}
          />
        </div>
      </div>
    )
  }

  // Owner 판별: 백엔드가 ``publication_summary.item_id`` 를 owner+link 동시일
  // 때만 채워주므로 (service.py `_project_item`), 이 한 줄로 충분하다.
  // origin_summary.source_user_id 는 다른 user 가 공유해준 케이스에만 set 되어
  // 본인 publish 에서는 null — 이전 비교는 항상 false 였다.
  const isOwner = !!user && item.publication_summary.item_id === item.id
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

  async function runMutation(
    action: () => Promise<unknown>,
    successMsg: string,
    options?: { fallback?: string; codeMap?: Record<string, string> },
  ) {
    try {
      await action()
      toast.success(successMsg)
    } catch (err) {
      const fallback = options?.fallback ?? t('toast.requestFailed')
      if (err instanceof ApiError) {
        toast.error(options?.codeMap?.[err.code ?? ''] ?? err.message ?? fallback)
      } else {
        toast.error(fallback)
      }
    }
  }

  async function handleDisable() {
    if (!item) return
    await runMutation(() => disableItem.mutateAsync(item.id), t('toast.disabled'), {
      fallback: t('toast.disableFailed'),
    })
  }

  async function handleEnable() {
    if (!item) return
    await runMutation(
      () => enableItem.mutateAsync(item.id),
      t('toast.enabled'),
      { fallback: t('toast.enableFailed') },
    )
  }

  async function handleUnpublish() {
    if (!item) return
    if (item.visibility === 'private') {
      toast.info(t('toast.alreadyPrivate'))
      return
    }
    await runMutation(
      () => patchItem.mutateAsync({ visibility: 'private' }),
      t('toast.unpublished'),
      { fallback: t('toast.unpublishFailed') },
    )
  }

  async function handleRevokeAcl(userId: string) {
    await runMutation(() => removeACL.mutateAsync(userId), t('toast.aclRevoked'), {
      fallback: t('toast.aclRevokeFailed'),
      codeMap: {
        marketplace_acl_required: t('toast.lastAclRequired'),
      },
    })
  }

  async function handleVisibilityChange(next: MarketplaceVisibility) {
    // ``system`` 은 super_user 시드만 — PATCH 로는 변경 불가.
    if (!item || next === item.visibility || next === 'system') return
    await runMutation(
      () => patchItem.mutateAsync({ visibility: next } satisfies MarketplaceItemPatchBody),
      t(`visibilitySuccess.${next}`),
      {
        fallback: t('toast.visibilityFailed'),
        codeMap: {
          marketplace_acl_required: t('toast.aclRequired'),
        },
      },
    )
  }

  return (
    <div className="moldy-app-surface flex flex-1 flex-col overflow-auto">
      <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-6 px-6 py-7 pb-20 md:px-8">
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
                <Button variant={cta.variant} disabled={cta.disabled} onClick={handlePrimary}>
                  {tCard(`cta.${cta.kind}`)}
                </Button>
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

        <div className="grid gap-4 lg:grid-cols-2">
          <Card className="border border-border bg-card">
            <CardHeader>
              <CardTitle className="text-sm">{t('versions.title')}</CardTitle>
            </CardHeader>
            <CardContent>
              {!versions || versions.length === 0 ? (
                <EmptyState
                  iconId={item.icon_id ?? getDomainIconIdForResource(item.resource_type)}
                  title={t('versions.emptyTitle')}
                  description={t('versions.emptyDescription')}
                />
              ) : (
                <ul className="space-y-2 text-sm">
                  {versions.map((v) => (
                    <li
                      key={v.id}
                      className="flex items-center justify-between gap-3 rounded-md border border-border/60 px-3 py-2"
                    >
                      <div className="min-w-0 space-y-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="font-medium">v{v.version_label}</span>
                          <Badge variant="secondary" className="moldy-ui-micro">
                            #{v.version_number}
                          </Badge>
                          {v.id === item.latest_version?.id ? (
                            <Badge className="bg-status-success/10 text-status-success moldy-ui-micro">
                              {t('versions.latest')}
                            </Badge>
                          ) : null}
                        </div>
                        {shortHash(v.content_hash) ? (
                          <code className="font-mono moldy-ui-caption text-muted-foreground">
                            {shortHash(v.content_hash)}
                          </code>
                        ) : null}
                      </div>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {formatMediumDate(v.created_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card className="border border-border bg-card">
            <CardHeader>
              <CardTitle className="text-sm">{t('executionProfile.title')}</CardTitle>
            </CardHeader>
            <CardContent className="text-sm text-muted-foreground">
              {item.execution_profile ? (
                <dl className="grid gap-2">
                  {Object.entries(item.execution_profile).map(([key, value]) => (
                    <div key={key} className="grid gap-1 rounded-md bg-muted/40 px-3 py-2 sm:grid-cols-[120px_1fr]">
                      <dt className="font-medium text-foreground">
                        {t(`executionProfile.labels.${key}`) || key}
                      </dt>
                      <dd>{formatExecutionProfileValue(key, value, t)}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p>{t('executionProfile.empty')}</p>
              )}
            </CardContent>
          </Card>
        </div>

        {isOwner || user?.is_super_user ? (
          <Card className="border border-border bg-card">
            <CardHeader>
              <CardTitle className="text-sm">
                {user?.is_super_user ? t('management.admin') : t('management.owner')}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {isOwner ? (
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-muted-foreground">{t('management.visibility')}</span>
                  <Select
                    value={item.visibility}
                    onValueChange={(v) => v && handleVisibilityChange(v as MarketplaceVisibility)}
                  >
                    <SelectTrigger className="w-[220px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="private">{t('visibility.private')}</SelectItem>
                      <SelectItem value="restricted">{t('visibility.restricted')}</SelectItem>
                      <SelectItem value="public">{t('visibility.public')}</SelectItem>
                      <SelectItem value="unlisted">{t('visibility.unlisted')}</SelectItem>
                    </SelectContent>
                  </Select>
                  {patchItem.isPending ? (
                    <span className="text-xs text-muted-foreground">{t('saving')}</span>
                  ) : null}
                </div>
              ) : null}
              <div className="flex flex-wrap gap-2">
                {isOwner && item.visibility !== 'private' ? (
                  <Button
                    variant="outline"
                    onClick={handleUnpublish}
                    disabled={patchItem.isPending}
                  >
                    {patchItem.isPending ? t('actions.unpublishing') : t('actions.unpublish')}
                  </Button>
                ) : null}
                {item.status === 'disabled' ? (
                  <Button onClick={handleEnable} disabled={enableItem.isPending}>
                    {enableItem.isPending ? t('actions.enabling') : t('actions.enable')}
                  </Button>
                ) : (
                  <Button
                    variant="outline"
                    onClick={handleDisable}
                    disabled={disableItem.isPending}
                  >
                    {disableItem.isPending ? t('actions.disabling') : t('actions.disable')}
                  </Button>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                {t.rich('management.help', {
                  private: (chunks) => <span className="font-medium">{chunks}</span>,
                  disabled: (chunks) => <span className="font-medium">{chunks}</span>,
                })}
              </p>

              {isOwner && item.visibility === 'restricted' && item.acl_user_ids ? (
                <div className="space-y-2 border-t border-border/60 pt-3">
                  <p className="text-sm font-medium">{t('acl.title')}</p>
                  {item.acl_user_ids.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      {t('acl.empty')}
                    </p>
                  ) : (
                    <ul className="space-y-1">
                      {item.acl_user_ids.map((uid) => (
                        <li
                          key={uid}
                          className="flex items-center justify-between gap-2 rounded-md bg-muted px-2 py-1.5 text-xs"
                        >
                          <code className="font-mono">{uid}</code>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => handleRevokeAcl(uid)}
                            disabled={removeACL.isPending}
                          >
                            {t('acl.revoke')}
                          </Button>
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="text-xs text-muted-foreground">
                    {t('acl.lastTargetHint')}
                  </p>
                </div>
              ) : null}
            </CardContent>
          </Card>
        ) : null}

        <InstallWizard item={item} open={installOpen} onOpenChange={setInstallOpen} />
        <UpdateStrategyDialog item={item} open={updateOpen} onOpenChange={setUpdateOpen} />
      </div>
    </div>
  )
}
