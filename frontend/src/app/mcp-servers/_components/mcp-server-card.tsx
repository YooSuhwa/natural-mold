'use client'

import { Activity, ChevronRight, Server } from 'lucide-react'

import { StatusChip } from '@/components/shared/status-chip'
import { Button } from '@/components/ui/button'
import {
  ResourceBadge,
  ResourceCardDescription,
  ResourceCardMeta,
  ResourceCardSubtext,
  ResourceCardTitle,
  ResourceListCard,
} from '@/components/shared/resource-layout'
import { getResourceTone, resourceStatusChipClassName } from '@/lib/resource-tones'
import type { HealthCheckEntry } from '@/lib/types/health'
import type { McpServer } from '@/lib/types/mcp'
import { cn } from '@/lib/utils'

type McpServerCardProps = {
  readonly server: McpServer
  readonly healthEntry: HealthCheckEntry | undefined
  readonly toolCountLabel: string
  readonly endpointLabel: string
  readonly lastResponseLabel: string
  readonly checkedAtLabel: string | null
  readonly checkNowLabel: string
  readonly checkNowAriaLabel: string
  readonly manageLabel: string
  readonly publishLabel: string
  readonly onOpen: (id: string) => void
  readonly onPublish: (server: McpServer) => void
  readonly onCheckNow: (id: string) => void
  readonly checking: boolean
}

export function McpServerCard({
  server,
  healthEntry,
  toolCountLabel,
  endpointLabel,
  lastResponseLabel,
  checkedAtLabel,
  checkNowLabel,
  checkNowAriaLabel,
  manageLabel,
  publishLabel,
  onOpen,
  onPublish,
  onCheckNow,
  checking,
}: McpServerCardProps) {
  const tone = getResourceTone(server.transport)
  const recencyLabel = checkedAtLabel || lastResponseLabel

  return (
    <ResourceListCard
      as="article"
      tone={tone}
      density="rich"
      aria-label={`${server.name} ${toolCountLabel}`}
    >
      <ResourceListCard.Header>
        <span className={cn('moldy-resource-icon', tone.icon)}>
          <Server className="size-4.5" />
        </span>
        <ResourceBadge tone={tone}>{server.transport}</ResourceBadge>
      </ResourceListCard.Header>

      <ResourceCardTitle>{server.name}</ResourceCardTitle>
      <ResourceCardDescription>{server.description ?? endpointLabel}</ResourceCardDescription>
      <ResourceCardSubtext tone="mono">{endpointLabel}</ResourceCardSubtext>

      <ResourceListCard.StatusRow>
        <StatusChip
          variant={healthEntry?.status ?? server.status}
          className={resourceStatusChipClassName}
        />
      </ResourceListCard.StatusRow>

      <ResourceListCard.MetaRow>
        <ResourceCardMeta>{toolCountLabel}</ResourceCardMeta>
        {recencyLabel ? <ResourceCardMeta>{recencyLabel}</ResourceCardMeta> : null}
      </ResourceListCard.MetaRow>

      <ResourceListCard.Footer className="justify-between">
        <div className="flex min-w-0 items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={checkNowAriaLabel}
            data-testid={`check-now-${server.id}`}
            className="h-7 px-2 text-xs"
            onClick={() => onCheckNow(server.id)}
            disabled={checking}
          >
            <Activity className="size-3.5" />
            {checkNowLabel}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => onPublish(server)}
          >
            {publishLabel}
          </Button>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={() => onOpen(server.id)}>
          {manageLabel}
          <ChevronRight className="size-3.5" />
        </Button>
      </ResourceListCard.Footer>
    </ResourceListCard>
  )
}
