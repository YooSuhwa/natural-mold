'use client'

import {
  PencilIcon,
  Trash2Icon,
  Loader2Icon,
  CheckCircleIcon,
  AlertTriangleIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { getProviderIcon } from '@/lib/utils/provider'
import type { Provider } from '@/lib/types'

interface ProviderCardProps {
  provider: Provider
  onEdit: (provider: Provider) => void
  onDelete: (id: string) => void
  isDeleting: boolean
  deletingId?: string
}

export function ProviderCard({
  provider,
  onEdit,
  onDelete,
  isDeleting,
  deletingId,
}: ProviderCardProps) {
  const t = useTranslations('provider')

  return (
    <Card>
      <CardContent className="flex items-center justify-between py-3">
        <div className="flex items-center gap-3">
          <div className="flex size-9 items-center justify-center rounded-lg bg-muted text-xs font-bold text-muted-foreground">
            {getProviderIcon(provider.provider_type)}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{provider.name}</span>
              <Badge variant="outline">{provider.provider_type}</Badge>
              {provider.has_api_key ? (
                <Badge variant="secondary">
                  <CheckCircleIcon className="mr-0.5 size-3" />
                  {t('connected')}
                </Badge>
              ) : (
                <Badge variant="ghost">
                  <AlertTriangleIcon className="mr-0.5 size-3" />
                  {t('noKey')}
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              {t('modelCount', { count: provider.model_count })}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t('editProvider')}
            onClick={() => onEdit(provider)}
          >
            <PencilIcon className="size-4 text-muted-foreground" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t('deleteLabel', { name: provider.name })}
            onClick={() => {
              if (window.confirm(t('deleteConfirm'))) {
                onDelete(provider.id)
              }
            }}
            disabled={isDeleting && deletingId === provider.id}
          >
            {isDeleting && deletingId === provider.id ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <Trash2Icon className="size-4 text-muted-foreground" />
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
