'use client'

import { AlertCircleIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'

interface Props {
  title?: string
  description?: string
  onRetry?: () => void
}

export function ErrorState({ title, description, onRetry }: Props) {
  const t = useTranslations('common.errorState')
  return (
    <div role="alert" className="moldy-error-state">
      <AlertCircleIcon className="size-8 text-status-danger" />
      <div>
        <p className="text-sm font-semibold text-foreground">{title ?? t('title')}</p>
        {description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          {t('retry')}
        </Button>
      ) : null}
    </div>
  )
}
