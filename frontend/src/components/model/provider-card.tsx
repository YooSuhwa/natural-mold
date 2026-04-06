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
import { getProviderIcon, getProviderLabel } from '@/lib/utils/provider'
import type { Provider } from '@/lib/types'

interface ProviderCardProps {
  provider: Provider
  onEdit: (provider: Provider) => void
  onDelete: () => void
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
    <Card className="transition-colors hover:border-primary/50">
      <CardContent className="flex items-center justify-between py-3">
        <div className="flex items-center gap-3">
          <div className="flex size-9 items-center justify-center rounded-lg bg-muted text-xs font-bold text-muted-foreground">
            {getProviderIcon(provider.provider_type)}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{provider.name}</span>
              <Badge variant="outline">{getProviderLabel(provider.provider_type)}</Badge>
              {provider.has_api_key ? (
                <Badge className="border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-950 dark:text-green-300">
                  <CheckCircleIcon className="mr-0.5 size-3" />
                  {t('connected')}
                </Badge>
              ) : (
                <Badge
                  className="cursor-pointer border-orange-200 bg-orange-50 text-orange-700 hover:bg-orange-100 dark:border-orange-800 dark:bg-orange-950 dark:text-orange-300 dark:hover:bg-orange-900"
                  onClick={() => onEdit(provider)}
                >
                  <AlertTriangleIcon className="mr-0.5 size-3" />
                  {t('setupApiKey')}
                </Badge>
              )}
              <Badge variant="secondary" className="text-[10px]">
                {t('modelCount', { count: provider.model_count })}
              </Badge>
            </div>
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
            onClick={onDelete}
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
