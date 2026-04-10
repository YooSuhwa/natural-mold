'use client'

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
import { useTranslations } from 'next-intl'

interface DeleteConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  onConfirm: () => void
  isPending?: boolean
  cancelLabel?: string
  confirmLabel?: string
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
}: DeleteConfirmDialogProps) {
  const t = useTranslations('common')
  const resolvedCancelLabel = cancelLabel ?? t('cancel')
  const resolvedConfirmLabel = confirmLabel ?? t('delete')
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description && <AlertDialogDescription>{description}</AlertDialogDescription>}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{resolvedCancelLabel}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onConfirm} disabled={isPending}>
            {resolvedConfirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
