'use client'

import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useApiErrorMessage } from '@/components/shared/api-error-state'
import { ApiError } from '@/lib/api/client'
import { useDisableItem, useEnableItem, usePatchMarketplaceItem } from '@/lib/hooks/use-marketplace'
import type {
  MarketplaceItem,
  MarketplaceItemPatchBody,
  MarketplaceVisibility,
} from '@/lib/types/marketplace'

interface Props {
  item: MarketplaceItem
  /** `can_manage` (owner or manager grant): visibility, unpublish, ACL (plan 13.9). */
  canManage: boolean
  isSuperUser: boolean
  onManageAcl: () => void
}

/** Owner/manager and operator controls of an item (moved out of the detail page). */
export function MarketplaceManagementCard({ item, canManage, isSuperUser, onManageAcl }: Props) {
  const t = useTranslations('marketplace.detail')
  const errorMessage = useApiErrorMessage()
  const disableItem = useDisableItem()
  const enableItem = useEnableItem()
  const patchItem = usePatchMarketplaceItem(item.id)

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
      const mapped = err instanceof ApiError ? options?.codeMap?.[err.code] : undefined
      toast.error(mapped ?? errorMessage(err, fallback))
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
    await runMutation(() => enableItem.mutateAsync(item.id), t('toast.enabled'), {
      fallback: t('toast.enableFailed'),
    })
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
    <Card className="border border-border bg-card">
      <CardHeader>
        <CardTitle className="text-sm">
          {isSuperUser ? t('management.admin') : t('management.owner')}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {canManage ? (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted-foreground">{t('management.visibility')}</span>
            <Select
              value={item.visibility}
              onValueChange={(v) => v && handleVisibilityChange(v as MarketplaceVisibility)}
            >
              <SelectTrigger className="w-56">
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
          {canManage && item.visibility !== 'private' ? (
            <Button variant="outline" onClick={handleUnpublish} disabled={patchItem.isPending}>
              {patchItem.isPending ? t('actions.unpublishing') : t('actions.unpublish')}
            </Button>
          ) : null}
          {item.status === 'disabled' ? (
            <Button onClick={handleEnable} disabled={enableItem.isPending}>
              {enableItem.isPending ? t('actions.enabling') : t('actions.enable')}
            </Button>
          ) : (
            <Button variant="outline" onClick={handleDisable} disabled={disableItem.isPending}>
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

        {canManage && item.visibility === 'restricted' ? (
          <div className="space-y-2 border-t border-border/60 pt-3">
            <p className="text-sm font-medium">{t('acl.title')}</p>
            <p className="text-xs text-muted-foreground">
              {t('acl.summary', { count: item.acl_user_ids?.length ?? 0 })}
            </p>
            <Button variant="outline" size="sm" onClick={onManageAcl}>
              {t('acl.manage')}
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
