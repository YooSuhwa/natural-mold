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
import type {
  MarketplaceItemPatchBody,
  MarketplaceVisibility,
} from '@/lib/types/marketplace'
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
  const enableItem = useEnableItem()
  const patchItem = usePatchMarketplaceItem(itemId)
  const removeACL = useRemoveItemACL(itemId)
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

  async function handleDisable() {
    if (!item) return
    try {
      await disableItem.mutateAsync(item.id)
      toast.success('Disabled')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to disable')
    }
  }

  async function handleEnable() {
    if (!item) return
    try {
      await enableItem.mutateAsync(item.id)
      toast.success('Re-enabled — 카탈로그 재노출은 visibility/listing 설정에 따라 결정됩니다.')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to enable')
    }
  }

  async function handleUnpublish() {
    if (!item) return
    if (item.visibility === 'private') {
      toast.info('이미 비공개 상태입니다.')
      return
    }
    try {
      await patchItem.mutateAsync({ visibility: 'private' })
      toast.success(
        'Unpublish 완료 — 카탈로그에서 미노출. 다른 사용자가 이미 install 한 copy 는 영향 없음.',
      )
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Unpublish 에 실패했습니다.')
    }
  }

  async function handleRevokeAcl(userId: string) {
    try {
      await removeACL.mutateAsync(userId)
      toast.success('공유 취소 완료')
    } catch (err) {
      toast.error(
        err instanceof ApiError
          ? err.code === 'marketplace_acl_required'
            ? 'restricted 상태에서는 마지막 ACL 을 비울 수 없습니다. visibility 를 먼저 바꾸세요.'
            : err.message
          : '공유 취소에 실패했습니다.',
      )
    }
  }

  async function handleVisibilityChange(next: MarketplaceVisibility) {
    if (!item || next === item.visibility) return
    // ``system`` 은 super_user 시드만 — PATCH 로는 변경 불가.
    if (next === 'system') return
    const body: MarketplaceItemPatchBody = { visibility: next }
    try {
      await patchItem.mutateAsync(body)
      const message =
        next === 'private'
          ? '비공개로 전환했습니다. 카탈로그에서 노출 안 됨.'
          : next === 'public'
            ? '공개로 전환했습니다. 카탈로그 노출은 super_user 승인 대기.'
            : next === 'unlisted'
              ? '링크 전용(unlisted)으로 전환했습니다.'
              : 'Restricted로 전환했습니다. ACL 대상 추가 필요.'
      toast.success(message)
    } catch (err) {
      toast.error(
        err instanceof ApiError
          ? err.code === 'marketplace_acl_required'
            ? 'restricted 전환은 ACL 대상이 최소 1명 필요합니다. ACL 추가 후 다시 시도하세요.'
            : err.message
          : 'Visibility 변경에 실패했습니다.',
      )
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
          <CardContent className="space-y-3">
            {isOwner ? (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-muted-foreground">Visibility:</span>
                <Select
                  value={item.visibility}
                  onValueChange={(v) =>
                    v && handleVisibilityChange(v as MarketplaceVisibility)
                  }
                >
                  <SelectTrigger className="w-[220px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="private">Private (me only)</SelectItem>
                    <SelectItem value="restricted">
                      Restricted (ACL users)
                    </SelectItem>
                    <SelectItem value="public">
                      Public (pending listing)
                    </SelectItem>
                    <SelectItem value="unlisted">Unlisted (link only)</SelectItem>
                  </SelectContent>
                </Select>
                {patchItem.isPending ? (
                  <span className="text-xs text-muted-foreground">Saving…</span>
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
                  {patchItem.isPending ? 'Unpublishing…' : 'Unpublish (Private 으로)'}
                </Button>
              ) : null}
              {item.status === 'disabled' ? (
                <Button
                  onClick={handleEnable}
                  disabled={enableItem.isPending}
                >
                  {enableItem.isPending ? 'Enabling…' : 'Re-enable item'}
                </Button>
              ) : (
                <Button
                  variant="outline"
                  onClick={handleDisable}
                  disabled={disableItem.isPending}
                >
                  {disableItem.isPending ? 'Disabling…' : 'Disable item'}
                </Button>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              <span className="font-medium">Unpublish</span>: visibility 를 private 으로 — 본인만 보임, 다른 사용자 신규 install 불가.{' '}
              <span className="font-medium">Disable</span>: 운영 차단 — 다른 사용자 신규 install 영구 차단. 둘 다 이미 install 된 copy 에는 영향 없음.
            </p>

            {isOwner && item.visibility === 'restricted' && item.acl_user_ids ? (
              <div className="space-y-2 border-t border-border/60 pt-3">
                <p className="text-sm font-medium">공유 대상 (Restricted ACL)</p>
                {item.acl_user_ids.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    공유된 user 가 없습니다. 새 user 추가는 ACL endpoint 직접
                    호출이 필요합니다 (후속 슬라이스에 wizard 추가 예정).
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
                          공유 취소
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="text-xs text-muted-foreground">
                  마지막 ACL 을 제거하려면 먼저 visibility 를 다른 값으로 변경하세요.
                </p>
              </div>
            ) : null}
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

