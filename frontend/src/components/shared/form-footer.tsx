'use client'

import type { ReactNode } from 'react'
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
  cancelLabel = 'Cancel',
  submitLabel = 'Save',
  pending,
  disabled,
  extraActions,
  submitForm,
  submitVariant = 'default',
}: Props) {
  return (
    <>
      {extraActions ? (
        <div className="mr-auto flex items-center gap-2">{extraActions}</div>
      ) : null}
      <Button
        variant="outline"
        onClick={onCancel}
        disabled={pending}
        className="min-w-[80px]"
      >
        {cancelLabel}
      </Button>
      <Button
        type={submitForm ? 'submit' : 'button'}
        form={submitForm}
        variant={submitVariant}
        onClick={onSubmit}
        disabled={disabled || pending}
        className="min-w-[80px]"
      >
        {pending ? <Loader2Icon className="mr-1 size-4 animate-spin" /> : null}
        {submitLabel}
      </Button>
    </>
  )
}
