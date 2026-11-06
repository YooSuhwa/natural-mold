'use client'

import { useState, type FormEvent, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

interface StopReasonDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  icon: ReactNode
  title: string
  description: string
  confirmLabel: string
  pending: boolean
  onSubmit: (reason: string) => void
}

/** A reason is required for an emergency stop and for its release (plan 17.4). */
export function StopReasonDialog(props: StopReasonDialogProps) {
  return <StopReasonDialogInner key={props.open ? 'open' : 'closed'} {...props} />
}

function StopReasonDialogInner({
  open,
  onOpenChange,
  icon,
  title,
  description,
  confirmLabel,
  pending,
  onSubmit,
}: StopReasonDialogProps) {
  const t = useTranslations('common')
  const tStop = useTranslations('emergencyStop')
  const [reason, setReason] = useState('')
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (reason.trim()) onSubmit(reason.trim())
  }
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="sm" height="auto">
      <DialogShell.Header icon={icon} title={title} description={description} />
      <DialogShell.Body>
        <form className="space-y-4" onSubmit={submit}>
          <Textarea
            aria-label={tStop('reason')}
            placeholder={tStop('reasonPlaceholder')}
            value={reason}
            maxLength={500}
            onChange={(event) => setReason(event.target.value)}
            rows={3}
          />
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t('cancel')}
            </Button>
            <Button type="submit" variant="destructive" disabled={pending || !reason.trim()}>
              {confirmLabel}
            </Button>
          </div>
        </form>
      </DialogShell.Body>
    </DialogShell>
  )
}
