'use client'

import { useTranslations } from 'next-intl'
import { Badge } from '@/components/ui/badge'
import type { ConnectionStatus } from '@/lib/types'

interface ConnectionStatusBadgeProps {
  status: ConnectionStatus
}

/** active=초록 outline / disabled=회색 secondary 일관 표시. card/detail-sheet 공용. */
export function ConnectionStatusBadge({ status }: ConnectionStatusBadgeProps) {
  const t = useTranslations('connections.card')
  const isDisabled = status === 'disabled'
  return (
    <Badge
      variant={isDisabled ? 'secondary' : 'outline'}
      className={`text-[10px] ${
        isDisabled ? 'text-muted-foreground' : 'border-emerald-300 text-emerald-700'
      }`}
    >
      {isDisabled ? t('statusDisabled') : t('statusActive')}
    </Badge>
  )
}
