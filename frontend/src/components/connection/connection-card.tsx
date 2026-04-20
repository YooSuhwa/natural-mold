'use client'

import {
  KeyRoundIcon,
  LinkIcon,
  MoreVerticalIcon,
  ServerIcon,
  WrenchIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { ConnectionStatusBadge } from '@/components/connection/connection-status-badge'
import { useCredential } from '@/lib/hooks/use-credentials'
import { useToolsByConnection } from '@/lib/hooks/use-tools'
import type { Connection } from '@/lib/types'

interface ConnectionCardProps {
  connection: Connection
  onOpenDetail: () => void
}

/**
 * Connection 1급 카드 — display_name · provider · status · credential · 사용 tool 카운트.
 * 카드 전체 클릭으로 상세 drawer 오픈. credential 이름은 useCredentials 캐시 lookup.
 */
export function ConnectionCard({ connection, onOpenDetail }: ConnectionCardProps) {
  const t = useTranslations('connections.card')
  const credential = useCredential(connection.credential_id)
  const tools = useToolsByConnection(connection)

  const credentialName = credential?.name ?? null

  const Icon =
    connection.type === 'prebuilt'
      ? KeyRoundIcon
      : connection.type === 'mcp'
        ? ServerIcon
        : WrenchIcon

  return (
    <Card
      role="region"
      aria-labelledby={`conn-${connection.id}-name`}
      className="cursor-pointer transition-colors hover:border-primary/40"
      onClick={onOpenDetail}
    >
      <CardContent className="flex items-center justify-between gap-3 py-3">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
            <Icon className="size-4 text-muted-foreground" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span id={`conn-${connection.id}-name`} className="truncate text-sm font-medium">
                {connection.display_name}
              </span>
              <Badge variant="outline" className="text-[10px]">
                {connection.provider_name}
              </Badge>
              {connection.is_default && (
                <Badge variant="secondary" className="text-[10px]">
                  {t('isDefaultBadge')}
                </Badge>
              )}
              <ConnectionStatusBadge status={connection.status} />
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <LinkIcon className="size-3" />
                {credentialName ?? t('credentialUnbound')}
              </span>
              <span className="flex items-center gap-1">
                <WrenchIcon className="size-3" />
                {tools.length > 0
                  ? t('usedByTools', { count: tools.length })
                  : t('noUsage')}
              </span>
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={(e) => {
              e.stopPropagation()
              onOpenDetail()
            }}
          >
            {t('openDetail')}
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t('openDetail')}
            onClick={(e) => {
              e.stopPropagation()
              onOpenDetail()
            }}
          >
            <MoreVerticalIcon className="size-4 text-muted-foreground" />
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
