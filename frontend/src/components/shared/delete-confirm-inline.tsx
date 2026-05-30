'use client'

import { useTranslations } from 'next-intl'
import { Loader2Icon } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface Props {
  entity: string
  onCancel: () => void
  onConfirm: () => void
  pending?: boolean
}

export function DeleteConfirmInline({ entity, onCancel, onConfirm, pending }: Props) {
  const t = useTranslations('common.deleteConfirm')
  const tc = useTranslations('common')

  return (
    <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs">
      <p className="font-medium text-destructive">{t('title', { entity })}</p>
      <div className="mt-2 flex gap-2">
        <Button size="sm" variant="outline" onClick={onCancel} disabled={pending}>
          {tc('cancel')}
        </Button>
        <Button
          size="sm"
          variant="destructive"
          onClick={onConfirm}
          disabled={pending}
        >
          {pending ? <Loader2Icon className="mr-1 size-3 animate-spin" /> : null}
          {t('confirm')}
        </Button>
      </div>
    </div>
  )
}
