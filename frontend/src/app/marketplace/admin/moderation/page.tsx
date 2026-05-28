'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import { CheckCircle2Icon, ShieldIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/shared/empty-state'
import { PageHeader } from '@/components/shared/page-header'
import { OriginBadge } from '@/components/marketplace/badges/origin-badge'
import { PublicationBadge } from '@/components/marketplace/badges/publication-badge'
import { useSession } from '@/lib/auth/session'
import { ApiError } from '@/lib/api/client'
import {
  useAdminSetListed,
  useDisableItem,
  useKSkillSyncStatus,
  useModerationQueue,
} from '@/lib/hooks/use-marketplace'
import { formatMediumDate } from '@/lib/utils/format-relative-time'

export default function MarketplaceModerationPage() {
  const router = useRouter()
  const { data: user, isLoading: userLoading } = useSession()
  const superUser = !!user?.is_super_user
  const { data: queue, isLoading } = useModerationQueue(superUser)
  const { data: kSkillStatus } = useKSkillSyncStatus(superUser)
  const disable = useDisableItem()
  const setListed = useAdminSetListed()

  if (userLoading) {
    return (
      <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
        <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-4 px-6 py-7 pb-20 md:px-8">
          <Skeleton className="h-8 w-48" />
        </div>
      </div>
    )
  }

  if (!superUser) {
    return (
      <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
        <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-4 px-6 py-7 pb-20 md:px-8">
          <EmptyState
            icon={<ShieldIcon className="size-6" />}
            title="운영자 전용 페이지"
            description="이 페이지는 운영자(super_user)만 접근할 수 있어요."
            action={
              <Button onClick={() => router.push('/marketplace')}>마켓플레이스로 돌아가기</Button>
            }
          />
        </div>
      </div>
    )
  }

  async function handleDisable(itemId: string) {
    try {
      await disable.mutateAsync(itemId)
      toast.success('비활성화 완료')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : '비활성화에 실패했어요')
    }
  }

  async function handleApprove(itemId: string) {
    try {
      await setListed.mutateAsync({ itemId, isListed: true })
      toast.success('카탈로그에 게시했어요')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : '승인에 실패했어요')
    }
  }

  return (
    <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
      <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-6 px-6 py-7 pb-20 md:px-8">
        <PageHeader
          title="마켓플레이스 운영"
          description="리스팅 승인이 필요한 공개 항목을 관리하세요."
        />

        <Card className="border border-border bg-card">
          <CardHeader>
            <CardTitle className="text-sm">대기 항목</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {isLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : !queue || queue.length === 0 ? (
              <EmptyState
                icon={<CheckCircle2Icon className="size-5" />}
                title="처리 대기 항목이 없어요"
                description="공개 게시된 항목이 모두 승인되었습니다."
              />
            ) : (
              <ul className="divide-y divide-border/60">
                {queue.map((item) => (
                  <li
                    key={item.id}
                    className="flex flex-wrap items-center justify-between gap-2 py-3"
                  >
                    <div className="min-w-0 flex-1">
                      <Link
                        href={`/marketplace/${item.id}`}
                        className="text-sm font-medium hover:text-primary-strong"
                      >
                        {item.name}
                      </Link>
                      <p className="text-xs text-muted-foreground">
                        {item.resource_type} · {formatMediumDate(item.created_at)}
                      </p>
                      <div className="mt-1 flex flex-wrap items-center gap-1">
                        <OriginBadge summary={item.origin_summary} />
                        <PublicationBadge summary={item.publication_summary} />
                      </div>
                    </div>
                    <div className="flex shrink-0 gap-1">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => router.push(`/marketplace/${item.id}`)}
                      >
                        검토
                      </Button>
                      <Button
                        size="sm"
                        disabled={setListed.isPending}
                        onClick={() => handleApprove(item.id)}
                      >
                        승인
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={disable.isPending}
                        onClick={() => handleDisable(item.id)}
                      >
                        비활성화
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card className="border border-border bg-card">
          <CardHeader>
            <CardTitle className="text-sm">k-skill 동기화 상태</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {kSkillStatus ? (
              <>
                <p>
                  <span className="text-muted-foreground">항목 수: </span>
                  <span className="font-medium">{kSkillStatus.count}</span>
                </p>
                <p>
                  <span className="text-muted-foreground">마지막 업데이트: </span>
                  <span className="font-medium">
                    {kSkillStatus.last_updated_at
                      ? formatMediumDate(kSkillStatus.last_updated_at)
                      : '—'}
                  </span>
                </p>
              </>
            ) : (
              <p className="text-muted-foreground">불러오는 중…</p>
            )}
            <pre className="rounded-md bg-muted px-3 py-2 text-xs">
              uv run python -m app.scripts.sync_k_skill --ref main
            </pre>
            <p className="text-xs text-muted-foreground">동기화는 CLI에서만 실행할 수 있어요.</p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
