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
      <div className="flex flex-1 flex-col gap-4 overflow-auto p-6">
        <Skeleton className="h-8 w-48" />
      </div>
    )
  }

  if (!superUser) {
    return (
      <div className="flex flex-1 flex-col gap-4 overflow-auto p-6">
        <EmptyState
          icon={<ShieldIcon className="size-6" />}
          title="Super user only"
          description="이 페이지는 운영자(super_user)만 접근할 수 있습니다."
          action={
            <Button onClick={() => router.push('/marketplace')}>
              Back to Marketplace
            </Button>
          }
        />
      </div>
    )
  }

  async function handleDisable(itemId: string) {
    try {
      await disable.mutateAsync(itemId)
      toast.success('Disabled')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to disable')
    }
  }

  async function handleApprove(itemId: string) {
    try {
      await setListed.mutateAsync({ itemId, isListed: true })
      toast.success('Listed in catalog')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to approve')
    }
  }

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader
        title="Marketplace Moderation"
        description="Pending public items awaiting listing approval."
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Pending items</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : !queue || queue.length === 0 ? (
            <EmptyState
              icon={<CheckCircle2Icon className="size-5" />}
              title="처리 대기 항목이 없습니다"
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
                      {item.resource_type} ·{' '}
                      {formatMediumDate(item.created_at)}
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
                      Review
                    </Button>
                    <Button
                      size="sm"
                      disabled={setListed.isPending}
                      onClick={() => handleApprove(item.id)}
                    >
                      Approve listing
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={disable.isPending}
                      onClick={() => handleDisable(item.id)}
                    >
                      Disable
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">k-skill sync status</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {kSkillStatus ? (
            <>
              <p>
                <span className="text-muted-foreground">Items: </span>
                <span className="font-medium">{kSkillStatus.count}</span>
              </p>
              <p>
                <span className="text-muted-foreground">Last updated: </span>
                <span className="font-medium">
                  {kSkillStatus.last_updated_at
                    ? formatMediumDate(kSkillStatus.last_updated_at)
                    : '—'}
                </span>
              </p>
            </>
          ) : (
            <p className="text-muted-foreground">Loading…</p>
          )}
          <pre className="rounded-md bg-muted px-3 py-2 text-xs">
            uv run python -m app.scripts.sync_k_skill --ref main
          </pre>
          <p className="text-xs text-muted-foreground">
            Sync는 CLI에서만 실행할 수 있습니다.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
