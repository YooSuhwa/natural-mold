'use client'

import { useState } from 'react'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogAction,
  AlertDialogCancel,
} from '@/components/ui/alert-dialog'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Input } from '@/components/ui/input'
import { useTranslations } from 'next-intl'

interface DeleteConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  /** With `confirmText`, receives the text the user typed (send it to the server check). */
  onConfirm: (typed?: string) => void
  isPending?: boolean
  cancelLabel?: string
  confirmLabel?: string
  /**
   * Plan 13.12.2 "이름 입력" variant: the confirm button stays disabled until this
   * exact text (a group name, an organization name, a company slug) is typed.
   */
  confirmText?: string
  /** Label of the confirmation input, shown with `confirmText`. */
  confirmTextLabel?: string
  /** Inline error (e.g. a failed request), under the confirmation input when present. */
  error?: string | null
}

export function DeleteConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  onConfirm,
  isPending,
  cancelLabel,
  confirmLabel,
  confirmText,
  confirmTextLabel,
  error,
}: DeleteConfirmDialogProps) {
  const t = useTranslations('common')
  const resolvedCancelLabel = cancelLabel ?? t('cancel')
  const resolvedConfirmLabel = confirmLabel ?? t('delete')
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description && (
            <AlertDialogDescription className="whitespace-pre-line">
              {description}
            </AlertDialogDescription>
          )}
        </AlertDialogHeader>
        {/* Keyed by `open`: the typed text never survives a close. */}
        <ConfirmTextFooter
          key={open ? 'open' : 'closed'}
          onConfirm={onConfirm}
          isPending={isPending}
          cancelLabel={resolvedCancelLabel}
          confirmLabel={resolvedConfirmLabel}
          confirmText={confirmText}
          confirmTextLabel={confirmTextLabel}
          error={error}
        />
      </AlertDialogContent>
    </AlertDialog>
  )
}

interface ConfirmTextFooterProps {
  onConfirm: (typed?: string) => void
  isPending?: boolean
  cancelLabel: string
  confirmLabel: string
  confirmText?: string
  confirmTextLabel?: string
  error?: string | null
}

function ConfirmTextFooter({
  onConfirm,
  isPending,
  cancelLabel,
  confirmLabel,
  confirmText,
  confirmTextLabel,
  error,
}: ConfirmTextFooterProps) {
  const [typed, setTyped] = useState('')
  const matches = confirmText === undefined || typed === confirmText
  return (
    <>
      {confirmText !== undefined ? (
        <FormFieldShell id="delete-confirm-text" label={confirmTextLabel ?? ''} error={error}>
          <Input
            id="delete-confirm-text"
            value={typed}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setTyped(event.target.value)}
          />
        </FormFieldShell>
      ) : error ? (
        <p role="alert" className="text-sm text-status-danger">
          {error}
        </p>
      ) : null}
      <AlertDialogFooter>
        <AlertDialogCancel>{cancelLabel}</AlertDialogCancel>
        <AlertDialogAction
          variant="destructive"
          onClick={() => (confirmText === undefined ? onConfirm() : onConfirm(typed))}
          disabled={isPending || !matches}
        >
          {confirmLabel}
        </AlertDialogAction>
      </AlertDialogFooter>
    </>
  )
}
