'use client'

import Link from 'next/link'
import { InfoIcon, Settings2Icon, SquarePenIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { Agent } from '@/lib/types'

interface AgentContextPopoverProps {
  agent: Agent | undefined
  agentId: string
}

export function AgentContextPopover({ agent, agentId }: AgentContextPopoverProps) {
  const t = useTranslations('agent.context')
  const toolCount = (agent?.tools?.length ?? 0) + (agent?.mcp_tools?.length ?? 0)
  const skillCount = agent?.skills?.length ?? 0

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="ghost" size="icon-sm" aria-label={t('trigger')} />}
      >
        <InfoIcon className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="bottom" sideOffset={6} className="w-72">
        {/* Base UI GroupLabel은 Menu.Group 내부에서만 렌더 가능하다 */}
        <DropdownMenuGroup>
          <DropdownMenuLabel>
            <span className="flex min-w-0 items-center gap-2">
              <AgentAvatar imageUrl={agent?.image_url ?? null} name={agent?.name ?? ''} size="xs" />
              <span className="truncate">{agent?.name ?? t('loading')}</span>
            </span>
          </DropdownMenuLabel>
          <div className="px-1.5 py-1.5">
            <p className="line-clamp-4 text-sm leading-relaxed text-muted-foreground">
              {agent?.description || t('noDescription')}
            </p>
            <div className="mt-2 grid grid-cols-3 gap-1 text-center">
              <div className="rounded-lg bg-muted px-1.5 py-1">
                <p className="moldy-ui-caption text-muted-foreground">{t('model')}</p>
                <p className="truncate moldy-ui-caption font-semibold">
                  {agent?.model?.display_name ?? t('unknown')}
                </p>
              </div>
              <div className="rounded-lg bg-muted px-1.5 py-1">
                <p className="moldy-ui-caption text-muted-foreground">{t('tools')}</p>
                <p className="moldy-ui-caption font-semibold">{toolCount}</p>
              </div>
              <div className="rounded-lg bg-muted px-1.5 py-1">
                <p className="moldy-ui-caption text-muted-foreground">{t('skills')}</p>
                <p className="moldy-ui-caption font-semibold">{skillCount}</p>
              </div>
            </div>
          </div>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem render={<Link href={`/agents/${agentId}/conversations/new`} />}>
          <SquarePenIcon />
          {t('newChat')}
        </DropdownMenuItem>
        <DropdownMenuItem render={<Link href={`/agents/${agentId}/settings`} />}>
          <Settings2Icon />
          {t('settings')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
