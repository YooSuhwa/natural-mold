'use client'

import { useTranslations } from 'next-intl'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useCatalogImpact } from '@/lib/hooks/use-org-governance'
import type { CatalogItemView } from '@/lib/types/org-governance'

interface CatalogDisableDialogProps {
  orgId: string
  item: CatalogItemView | null
  isPending: boolean
  onCancel: () => void
  onConfirm: (item: CatalogItemView) => void
}

/** S09 끄기 확인: affected/required agent counts from `GET .../catalog-policy/impact`. */
export function CatalogDisableDialog({
  orgId,
  item,
  isPending,
  onCancel,
  onConfirm,
}: CatalogDisableDialogProps) {
  const t = useTranslations('orgAdmin.catalog.disable')
  const tCommon = useTranslations('orgAdmin.common')
  const impact = useCatalogImpact(orgId, item ? { type: item.type, id: item.id } : null)

  return (
    <DialogShell
      open={item !== null}
      onOpenChange={(open) => (open ? undefined : onCancel())}
      size="sm"
      height="compact"
    >
      <DialogShell.Header
        title={t('title', { name: item?.name ?? '' })}
        description={t('description')}
      />
      <DialogShell.Body>
        {impact.isLoading ? (
          <Skeleton className="h-12 w-full" />
        ) : impact.data ? (
          <div className="space-y-1 text-sm">
            <p>{t('affected', { count: impact.data.affected_agents })}</p>
            {impact.data.required_agents > 0 ? (
              <p className="text-status-danger">
                {t('required', { count: impact.data.required_agents })}
              </p>
            ) : null}
          </div>
        ) : (
          <ApiErrorState error={impact.error} onRetry={() => void impact.refetch()} />
        )}
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onCancel}>
          {tCommon('cancel')}
        </Button>
        <Button
          variant="destructive"
          // Fail closed: no confirmation without the impact preview (S09).
          disabled={!item || !impact.data || isPending}
          onClick={() => {
            if (item) onConfirm(item)
          }}
        >
          {t('submit')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
