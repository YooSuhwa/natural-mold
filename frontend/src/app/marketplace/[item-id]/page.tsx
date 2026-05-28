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
import type { MarketplaceItemPatchBody, MarketplaceVisibility } from '@/lib/types/marketplace'
import { formatMediumDate } from '@/lib/utils/format-relative-time'

const VISIBILITY_SUCCESS_MESSAGE: Record<Exclude<MarketplaceVisibility, 'system'>, string> = {
  private: '비공개로 전환했습니다. 카탈로그에서 노출 안 됨.',
  public: '공개로 전환했습니다. 카탈로그 노출은 super_user 승인 대기.',
  unlisted: '링크 전용(unlisted)으로 전환했습니다.',
  restricted: 'Restricted로 전환했습니다. ACL 대상 추가 필요.',
}

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
      <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
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
      <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
        <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-4 px-6 py-7 pb-20 md:px-8">
          <Link
            href="/marketplace"
            className="inline-flex items-center gap-1 text-sm text-primary-strong"
          >
            <ChevronLeftIcon className="size-4" />
            마켓플레이스로 돌아가기
          </Link>
          <ErrorState
            title={notFound ? '항목을 찾을 수 없어요' : '항목을 불러오지 못했어요'}
            description="이 항목을 찾을 수 없거나 접근 권한이 없습니다."
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
      const fallback = options?.fallback ?? '요청에 실패했습니다.'
      if (err instanceof ApiError) {
        toast.error(options?.codeMap?.[err.code ?? ''] ?? err.message ?? fallback)
      } else {
        toast.error(fallback)
      }
    }
  }

  async function handleDisable() {
    if (!item) return
    await runMutation(() => disableItem.mutateAsync(item.id), '비활성화 완료', {
      fallback: '비활성화에 실패했습니다.',
    })
  }

  async function handleEnable() {
    if (!item) return
    await runMutation(
      () => enableItem.mutateAsync(item.id),
      '재활성화 완료 — 카탈로그 재노출은 공개 범위/리스팅 설정에 따라 결정됩니다.',
      { fallback: '재활성화에 실패했습니다.' },
    )
  }

  async function handleUnpublish() {
    if (!item) return
    if (item.visibility === 'private') {
      toast.info('이미 비공개 상태입니다.')
      return
    }
    await runMutation(
      () => patchItem.mutateAsync({ visibility: 'private' }),
      '비공개 전환 완료 — 카탈로그에서 미노출. 이미 설치된 사본은 영향 없음.',
      { fallback: '비공개 전환에 실패했습니다.' },
    )
  }

  async function handleRevokeAcl(userId: string) {
    await runMutation(() => removeACL.mutateAsync(userId), '공유 취소 완료', {
      fallback: '공유 취소에 실패했습니다.',
      codeMap: {
        marketplace_acl_required:
          'restricted 상태에서는 마지막 ACL 을 비울 수 없습니다. visibility 를 먼저 바꾸세요.',
      },
    })
  }

  async function handleVisibilityChange(next: MarketplaceVisibility) {
    // ``system`` 은 super_user 시드만 — PATCH 로는 변경 불가.
    if (!item || next === item.visibility || next === 'system') return
    await runMutation(
      () => patchItem.mutateAsync({ visibility: next } satisfies MarketplaceItemPatchBody),
      VISIBILITY_SUCCESS_MESSAGE[next],
      {
        fallback: '공개 범위 변경에 실패했습니다.',
        codeMap: {
          marketplace_acl_required:
            '제한 공유 전환은 공유 대상이 최소 1명 필요합니다. 대상 추가 후 다시 시도하세요.',
        },
      },
    )
  }

  return (
    <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
      <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-6 px-6 py-7 pb-20 md:px-8">
        <Link
          href="/marketplace"
          className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ChevronLeftIcon className="size-4" />
          마켓플레이스로 돌아가기
        </Link>

        <PageHeader
          title={item.name}
          description={item.description ?? undefined}
          action={
            <div className="flex items-center gap-2">
              <Button variant={cta.variant} disabled={cta.disabled} onClick={handlePrimary}>
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
          <Card className="border border-border bg-card">
            <CardHeader>
              <CardTitle className="text-sm">버전</CardTitle>
            </CardHeader>
            <CardContent>
              {!versions || versions.length === 0 ? (
                <EmptyState
                  icon={<SparklesIcon className="size-5" />}
                  title="아직 공개된 버전이 없어요"
                  description="스킬 상세 페이지에서 첫 버전을 공개할 수 있어요."
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

          <Card className="border border-border bg-card">
            <CardHeader>
              <CardTitle className="text-sm">실행 프로필</CardTitle>
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
                <p>등록된 실행 프로필이 없어요.</p>
              )}
            </CardContent>
          </Card>
        </div>

        {isOwner || user?.is_super_user ? (
          <Card className="border border-border bg-card">
            <CardHeader>
              <CardTitle className="text-sm">
                {user?.is_super_user ? '운영 관리' : '소유자 관리'}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {isOwner ? (
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-muted-foreground">공개 범위:</span>
                  <Select
                    value={item.visibility}
                    onValueChange={(v) => v && handleVisibilityChange(v as MarketplaceVisibility)}
                  >
                    <SelectTrigger className="w-[220px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="private">비공개 (나만)</SelectItem>
                      <SelectItem value="restricted">제한 공유 (지정 사용자)</SelectItem>
                      <SelectItem value="public">공개 (리스팅 대기)</SelectItem>
                      <SelectItem value="unlisted">링크 전용</SelectItem>
                    </SelectContent>
                  </Select>
                  {patchItem.isPending ? (
                    <span className="text-xs text-muted-foreground">저장 중…</span>
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
                    {patchItem.isPending ? '비공개 전환 중…' : '비공개로 전환'}
                  </Button>
                ) : null}
                {item.status === 'disabled' ? (
                  <Button onClick={handleEnable} disabled={enableItem.isPending}>
                    {enableItem.isPending ? '재활성화 중…' : '항목 재활성화'}
                  </Button>
                ) : (
                  <Button
                    variant="outline"
                    onClick={handleDisable}
                    disabled={disableItem.isPending}
                  >
                    {disableItem.isPending ? '비활성화 중…' : '항목 비활성화'}
                  </Button>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                <span className="font-medium">비공개 전환</span>: 본인만 보이며 다른 사용자의 신규
                설치가 막혀요. <span className="font-medium">비활성화</span>: 운영 차단 — 다른
                사용자의 신규 설치가 영구 차단됩니다. 두 경우 모두 이미 설치된 사본에는 영향이
                없어요.
              </p>

              {isOwner && item.visibility === 'restricted' && item.acl_user_ids ? (
                <div className="space-y-2 border-t border-border/60 pt-3">
                  <p className="text-sm font-medium">공유 대상 (제한 공유)</p>
                  {item.acl_user_ids.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      공유된 사용자가 없어요. 새 사용자 추가는 ACL 엔드포인트 직접 호출이 필요합니다
                      (후속 슬라이스에 마법사 추가 예정).
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
                    마지막 대상을 제거하려면 먼저 공개 범위를 다른 값으로 변경하세요.
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
