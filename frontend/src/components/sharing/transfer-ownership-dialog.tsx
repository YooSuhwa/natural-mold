'use client'

import { useId, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import type { ShareResourceType } from '@/lib/types/grant'

import { PrincipalPicker, principalKey } from './principal-picker'
import { useResourceShareBackend, type ShareSubject } from './share-backend'
import { shareErrorMessage, transferBlockers, type TransferBlocker } from './share-error'

interface TransferOwnershipDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  resourceType: ShareResourceType
  resourceId: string
  resourceName: string
  /** Current owner: never offered as the new owner. */
  ownerId: string
  /** Owners may keep a manager grant; governors must give a reason (10.7). */
  isOwner: boolean
  onTransferred?: () => void
}

/** S17 ownership transfer over the E-01 `POST …/transfer` API. */
export function TransferOwnershipDialog(props: TransferOwnershipDialogProps) {
  const t = useTranslations('sharing.transferDialog')
  return (
    <DialogShell open={props.open} onOpenChange={props.onOpenChange} size="md" height="auto">
      <DialogShell.Header title={t('title')} description={props.resourceName} />
      {props.open ? <TransferBody key={props.resourceId} {...props} /> : null}
    </DialogShell>
  )
}

function TransferBody({
  onOpenChange,
  resourceType,
  resourceId,
  resourceName,
  ownerId,
  isOwner,
  onTransferred,
}: TransferOwnershipDialogProps) {
  const t = useTranslations('sharing.transferDialog')
  const tTransfer = useTranslations('sharing.transfer')
  const tSharing = useTranslations('sharing')
  const reasonId = useId()
  const keepId = useId()
  const backend = useResourceShareBackend(resourceType, resourceId)
  const [owner, setOwner] = useState<ShareSubject | null>(null)
  const [reason, setReason] = useState('')
  const [keepManager, setKeepManager] = useState(true)
  const [confirming, setConfirming] = useState(false)
  const [blockers, setBlockers] = useState<TransferBlocker[]>([])
  const [error, setError] = useState<string | null>(null)

  const snapshot = backend.snapshot
  // Fail closed: no submit until the access list proves the caller may govern.
  const canGovern = snapshot?.canGovern === true && backend.transfer !== null
  const reasonMissing = !isOwner && reason.trim().length === 0
  const delegations = snapshot?.delegations?.length ?? 0

  async function run() {
    if (!owner || !backend.transfer) return
    setError(null)
    try {
      await backend.transfer({
        new_owner_id: owner.id,
        reason: reason.trim() || null,
        keep_manager: isOwner && keepManager,
      })
      toast.success(tTransfer('done', { name: owner.displayName }))
      onTransferred?.()
      onOpenChange(false)
    } catch (err) {
      setBlockers(transferBlockers(err))
      setError(shareErrorMessage(err, tSharing))
    } finally {
      setConfirming(false)
    }
  }

  return (
    <>
      <DialogShell.Body>
        {backend.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : !snapshot ? (
          <ApiErrorState error={backend.error} onRetry={backend.refetch} />
        ) : !canGovern ? (
          <p role="alert" className="text-sm text-status-danger">
            {t('noPermission')}
          </p>
        ) : (
          <div className="space-y-4">
            {owner ? (
              <div className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm">
                <span className="truncate">{owner.displayName}</span>
                <Button type="button" variant="ghost" size="sm" onClick={() => setOwner(null)}>
                  {tTransfer('change')}
                </Button>
              </div>
            ) : (
              <PrincipalPicker
                types={['user']}
                excludeKeys={new Set([principalKey('user', ownerId)])}
                label={tTransfer('newOwner')}
                onPick={setOwner}
              />
            )}
            {isOwner ? (
              <div className="flex items-center gap-2 text-sm">
                <Checkbox
                  aria-labelledby={keepId}
                  checked={keepManager}
                  onCheckedChange={(checked) => setKeepManager(checked === true)}
                />
                <span id={keepId}>{tTransfer('keepManager')}</span>
              </div>
            ) : null}
            <div className="space-y-1.5">
              <label htmlFor={reasonId} className="text-sm">
                {isOwner ? tTransfer('reasonOptional') : tTransfer('reasonRequired')}
              </label>
              <Textarea
                id={reasonId}
                value={reason}
                maxLength={500}
                aria-required={!isOwner}
                onChange={(event) => setReason(event.target.value)}
              />
            </div>
            {delegations > 0 ? (
              <p className="text-xs text-muted-foreground">
                {t('delegations', { count: delegations })}
              </p>
            ) : null}
            {error ? (
              <div role="alert" className="space-y-1 text-xs text-status-danger">
                <p className="font-medium">{error}</p>
                {blockers.length > 0 ? (
                  <ul className="list-disc pl-5">
                    {blockers.map((item) => (
                      <li key={item.kind}>
                        {tTransfer(`blocker.${item.kind}`, { count: item.count })}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}
          </div>
        )}
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          {t('cancel')}
        </Button>
        <Button
          variant="destructive"
          disabled={!canGovern || !owner || reasonMissing || backend.pending}
          onClick={() => setConfirming(true)}
        >
          {tTransfer('submit')}
        </Button>
      </DialogShell.Footer>
      <DeleteConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={tTransfer('confirmTitle', {
          resource: resourceName,
          name: owner?.displayName ?? '',
        })}
        description={tTransfer('confirmDescription')}
        confirmLabel={tTransfer('submit')}
        isPending={backend.pending}
        onConfirm={() => void run()}
      />
    </>
  )
}
