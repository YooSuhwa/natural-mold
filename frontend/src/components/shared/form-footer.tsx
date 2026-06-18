'use client'

import type { ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { Loader2Icon } from 'lucide-react'

import { Button } from '@/components/ui/button'

interface Props {
  onCancel: () => void
  onSubmit?: () => void
  cancelLabel?: ReactNode
  submitLabel?: ReactNode
  pending?: boolean
  disabled?: boolean
  extraActions?: ReactNode
  submitForm?: string
  submitVariant?: 'default' | 'destructive' | 'secondary'
}

export function FormFooter({
  onCancel,
  onSubmit,
  cancelLabel,
  submitLabel,
  pending,
  disabled,
  extraActions,
  submitForm,
  submitVariant = 'default',
}: Props) {
  const t = useTranslations('common')

  return (
    <>
      {extraActions ? <div className="mr-auto flex items-center gap-2">{extraActions}</div> : null}
      <Button variant="outline" onClick={onCancel} disabled={pending} className="min-w-20">
        {cancelLabel ?? t('cancel')}
      </Button>
      <Button
        type={submitForm ? 'submit' : 'button'}
        form={submitForm}
        variant={submitVariant}
        onClick={onSubmit}
        disabled={disabled || pending}
        className="min-w-20"
      >
        {pending ? <Loader2Icon className="mr-1 size-4 animate-spin" /> : null}
        {submitLabel ?? t('save')}
      </Button>
    </>
  )
}
