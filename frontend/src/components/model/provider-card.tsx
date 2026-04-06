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
import type { Provider } from '@/lib/types'

function getProviderIcon(type: string) {
  switch (type) {
    case 'openai':
      return 'OAI'
    case 'anthropic':
      return 'ANT'
    case 'google':
      return 'GGL'
    case 'openrouter':
      return 'ORT'
    case 'openai_compatible':
      return 'LCL'
    default:
      return 'AI'
  }
}

interface ProviderCardProps {
  provider: Provider
  onEdit: (provider: Provider) => void
  onDelete: (id: string) => void
  isDeleting: boolean
}

export function ProviderCard({ provider, onEdit, onDelete, isDeleting }: ProviderCardProps) {
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
          <Button variant="ghost" size="icon-sm" onClick={() => onEdit(provider)}>
            <PencilIcon className="size-4 text-muted-foreground" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => onDelete(provider.id)}
            disabled={isDeleting}
          >
            {isDeleting ? (
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

export { getProviderIcon }
