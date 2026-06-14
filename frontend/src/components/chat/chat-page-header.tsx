'use client'

import {
  ActivityIcon,
  FilesIcon,
  MoreHorizontalIcon,
  Settings2Icon,
  SquarePenIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AgentContextPopover } from '@/components/agent/agent-context-popover'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import type { Agent } from '@/lib/types'

interface ChatPageHeaderProps {
  readonly agent: Agent | undefined
  readonly agentId: string
  readonly title: string | null | undefined
  readonly onNewConversation: () => void
  readonly onOpenSettings: () => void
  readonly onOpenTrace: () => void
  readonly onToggleArtifacts: () => void
}

export function ChatPageHeader({
  agent,
  agentId,
  title,
  onNewConversation,
  onOpenSettings,
  onOpenTrace,
  onToggleArtifacts,
}: ChatPageHeaderProps) {
  const t = useTranslations('chat.page')

  return (
    <div className="moldy-panel-header flex items-center justify-between px-4 py-2.5">
      <div className="flex min-w-0 items-center gap-2">
        <h1 className="truncate text-sm font-semibold">
          {title ?? agent?.name ?? <Skeleton className="inline-block h-4 w-24" />}
        </h1>
        <AgentContextPopover agent={agent} agentId={agentId} />
      </div>
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('artifacts')}
          onClick={onToggleArtifacts}
        >
          <FilesIcon className="size-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('traceDebugger')}
          onClick={onOpenTrace}
        >
          <ActivityIcon className="size-4" />
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={<Button variant="ghost" size="icon-sm" aria-label={t('menu')} />}
          >
            <MoreHorizontalIcon className="size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={onNewConversation}>
              <SquarePenIcon />
              {t('newConversation')}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onOpenSettings}>
              <Settings2Icon />
              {t('settings')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  )
}
