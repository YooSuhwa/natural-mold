'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { ReceiptTextIcon } from 'lucide-react'
import { toast } from 'sonner'

import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api/client'
import { useDeleteMemoryScope, useMemoryScopeCount } from '@/lib/hooks/use-memory'
import type { MemoryDeletionReceipt, MemoryScopeQuery } from '@/lib/types'

/**
 * Plan 15 (5.7) 전체 삭제: fetch the current count for the scope, make the person type it,
 * send it as `confirm_count`, then show the deletion receipt (what stays elsewhere too).
 * Mount it with `active` while the person is confirming; `onClose` ends the flow.
 */
export function MemoryBulkDeleteFlow({
  query,
  active,
  onClose,
}: {
  query: MemoryScopeQuery
  active: boolean
  onClose: () => void
}) {
  const t = useTranslations('appSettings.memory.bulkDelete')
  const tScope = useTranslations('appSettings.memory.scopeSelect')
  const count = useMemoryScopeCount(query, active)
  const remove = useDeleteMemoryScope()
  const [receipt, setReceipt] = useState<MemoryDeletionReceipt | null>(null)
  const current = count.data?.count

  function handleConfirm() {
    if (current === undefined) return
    remove.mutate(
      {
        scope: query.scope,
        agent_id: query.scope === 'agent' ? (query.agentId ?? null) : null,
        confirm_count: current,
      },
      {
        onSuccess: (result) => {
          setReceipt(result)
          onClose()
        },
        onError: (error) => {
          if (error instanceof ApiError && error.code === 'MEMORY_BULK_COUNT_CHANGED') {
            toast.error(t('countChanged'))
            void count.refetch()
            return
          }
          toast.error(error instanceof Error ? error.message : t('failed'))
        },
      },
    )
  }

  return (
    <>
      {active && current === 0 ? (
        <p role="status" className="text-sm text-muted-foreground" data-testid="memory-bulk-empty">
          {t('empty')}
        </p>
      ) : null}
      <DeleteConfirmDialog
        open={active && current !== undefined && current > 0}
        onOpenChange={(open) => {
          if (!open) onClose()
        }}
        title={t('confirmTitle', { count: current ?? 0 })}
        description={t('confirmDescription', {
          count: current ?? 0,
          scope: tScope(query.scope),
        })}
        confirmText={current !== undefined ? String(current) : undefined}
        confirmTextLabel={t('confirmTextLabel', { count: current ?? 0 })}
        confirmLabel={t('submit')}
        isPending={remove.isPending || count.isFetching}
        onConfirm={handleConfirm}
      />
      <MemoryDeletionReceiptDialog receipt={receipt} onClose={() => setReceipt(null)} />
    </>
  )
}

function MemoryDeletionReceiptDialog({
  receipt,
  onClose,
}: {
  receipt: MemoryDeletionReceipt | null
  onClose: () => void
}) {
  const t = useTranslations('appSettings.memory.bulkDelete.receipt')
  const tCommon = useTranslations('common')
  const places = (receipt?.kept_elsewhere ?? []).map((place) =>
    t.has(`place.${place}`) ? t(`place.${place}`) : place,
  )
  return (
    <DialogShell
      open={receipt !== null}
      onOpenChange={(open) => !open && onClose()}
      size="sm"
      height="auto"
    >
      <DialogShell.Header icon={<ReceiptTextIcon className="size-4" />} title={t('title')} />
      <DialogShell.Body>
        {receipt ? (
          <div className="space-y-3" data-testid="memory-deletion-receipt">
            <ul className="space-y-1 text-sm text-foreground">
              <li>{t('deleted', { count: receipt.deleted })}</li>
              <li>{t('revisions', { count: receipt.revisions_removed })}</li>
              <li>{t('proposals', { count: receipt.proposals_cleared })}</li>
              <li>{t('derived', { count: receipt.derived_marked })}</li>
            </ul>
            {places.length > 0 ? (
              <p className="text-sm text-muted-foreground">
                {t('kept', { places: places.join(', ') })}
              </p>
            ) : null}
          </div>
        ) : null}
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button type="button" onClick={onClose}>
          {tCommon('close')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
