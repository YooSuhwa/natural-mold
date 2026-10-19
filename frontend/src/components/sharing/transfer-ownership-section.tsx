'use client'

import { useId, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Textarea } from '@/components/ui/textarea'

import { PrincipalPicker } from './principal-picker'
import type { ShareBackend, ShareSubject } from './share-backend'
import { shareErrorMessage, transferBlockers, type TransferBlocker } from './share-error'

interface TransferOwnershipSectionProps {
  backend: ShareBackend
  resourceName: string
  /** Governors (not the owner) must give a reason (10.7). */
  isOwner: boolean
  ownerKey: string | null
  /** Called when the transfer leaves the caller without sharing rights. */
  onLostAccess: () => void
}

export function TransferOwnershipSection({
  backend,
  resourceName,
  isOwner,
  ownerKey,
  onLostAccess,
}: TransferOwnershipSectionProps) {
  const t = useTranslations('sharing.transfer')
  const tSharing = useTranslations('sharing')
  const regionId = useId()
  const reasonId = useId()
  const keepId = useId()
  const [expanded, setExpanded] = useState(false)
  const [owner, setOwner] = useState<ShareSubject | null>(null)
  const [reason, setReason] = useState('')
  const [keepManager, setKeepManager] = useState(true)
  const [confirming, setConfirming] = useState(false)
  const [blockers, setBlockers] = useState<TransferBlocker[]>([])
  const transfer = backend.transfer
  if (!transfer || !backend.snapshot?.canGovern) return null
  const reasonMissing = !isOwner && reason.trim().length === 0

  async function run() {
    if (!owner || !transfer) return
    // Only an owner who keeps a manager grant stays in a dialog that still applies.
    const keepsAccess = isOwner && keepManager
    try {
      await transfer({
        new_owner_id: owner.id,
        reason: reason.trim() || null,
        keep_manager: isOwner && keepManager,
      })
      toast.success(t('done', { name: owner.displayName }))
      setOwner(null)
      setReason('')
      setBlockers([])
      setExpanded(false)
      if (!keepsAccess) onLostAccess()
    } catch (error) {
      setBlockers(transferBlockers(error))
      toast.error(shareErrorMessage(error, tSharing))
    } finally {
      setConfirming(false)
    }
  }

  return (
    <section className="space-y-3 border-t border-border/60 pt-4">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-expanded={expanded}
        aria-controls={regionId}
        onClick={() => setExpanded((value) => !value)}
      >
        {t('toggle')}
      </Button>
      <div id={regionId} hidden={!expanded} className="space-y-3">
        <p className="text-xs text-muted-foreground">{t('description')}</p>
        {owner ? (
          <div className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm">
            <span className="truncate">{owner.displayName}</span>
            <Button type="button" variant="ghost" size="sm" onClick={() => setOwner(null)}>
              {t('change')}
            </Button>
          </div>
        ) : (
          <PrincipalPicker
            types={['user']}
            excludeKeys={ownerKey ? new Set([ownerKey]) : undefined}
            label={t('newOwner')}
            onPick={setOwner}
          />
        )}
        <div className="space-y-1.5">
          <label htmlFor={reasonId}>{isOwner ? t('reasonOptional') : t('reasonRequired')}</label>
          <Textarea
            id={reasonId}
            value={reason}
            maxLength={500}
            aria-required={!isOwner}
            onChange={(event) => setReason(event.target.value)}
          />
        </div>
        {isOwner ? (
          <div className="flex items-center gap-2 text-sm">
            <Checkbox
              aria-labelledby={keepId}
              checked={keepManager}
              onCheckedChange={(checked) => setKeepManager(checked === true)}
            />
            <span id={keepId}>{t('keepManager')}</span>
          </div>
        ) : null}
        {blockers.length > 0 ? (
          <div role="alert" className="rounded-md border border-destructive/40 px-3 py-2 text-xs">
            <p className="font-medium">{t('blockedTitle')}</p>
            <ul className="list-disc pl-5">
              {blockers.map((item) => (
                <li key={item.kind}>{t(`blocker.${item.kind}`, { count: item.count })}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="flex justify-end">
          <Button
            type="button"
            variant="destructive"
            disabled={!owner || reasonMissing || backend.pending}
            onClick={() => setConfirming(true)}
          >
            {t('submit')}
          </Button>
        </div>
      </div>
      <DeleteConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t('confirmTitle', { resource: resourceName, name: owner?.displayName ?? '' })}
        description={t('confirmDescription')}
        confirmLabel={t('submit')}
        isPending={backend.pending}
        onConfirm={() => void run()}
      />
    </section>
  )
}
