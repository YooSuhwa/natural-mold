'use client'

import Link from 'next/link'
import { PlusIcon, SearchIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { SidebarGroupLabel } from '@/components/ui/sidebar'
import type { AgentSort, AgentSummary, ConversationSort, NavigatorMode } from '@/lib/types'
import { ChatNavigatorMenu } from './chat-navigator-menu'

interface ChatNavigatorHeaderProps {
  activeAgentId: string | null
  sortedAgents: readonly AgentSummary[]
  mode: NavigatorMode
  agentSort: AgentSort
  sessionSort: ConversationSort
  singleExpandedAgent: boolean
  onOpenSearch: () => void
  onModeChange: (mode: NavigatorMode) => void
  onAgentSortChange: (sort: AgentSort) => void
  onSessionSortChange: (sort: ConversationSort) => void
  onSingleExpandedAgentChange: (enabled: boolean) => void
}

export function ChatNavigatorHeader({
  activeAgentId,
  sortedAgents,
  mode,
  agentSort,
  sessionSort,
  singleExpandedAgent,
  onOpenSearch,
  onModeChange,
  onAgentSortChange,
  onSessionSortChange,
  onSingleExpandedAgentChange,
}: ChatNavigatorHeaderProps) {
  const t = useTranslations('sidebar.agents')

  return (
    <SidebarGroupLabel className="group/nav-heading flex items-center justify-between group-data-[collapsible=icon]:hidden">
      <span>{t('title')}</span>
      <span className="flex items-center gap-0.5">
        {activeAgentId ? (
          <Button
            variant="ghost"
            size="icon-xs"
            render={<Link href={`/agents/${activeAgentId}/conversations/new`} />}
            aria-label={t('newChat')}
          >
            <PlusIcon className="size-3.5" />
          </Button>
        ) : (
          <DropdownMenu>
            <DropdownMenuTrigger
              render={<Button variant="ghost" size="icon-xs" aria-label={t('newChat')} />}
            >
              <PlusIcon className="size-3.5" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" side="bottom" sideOffset={4}>
              {sortedAgents.slice(0, 6).map((agent) => (
                <DropdownMenuItem
                  key={agent.id}
                  render={<Link href={`/agents/${agent.id}/conversations/new`} />}
                >
                  <AgentAvatar imageUrl={agent.image_url} name={agent.name} size="xs" />
                  {agent.name}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={t('searchToggle')}
          onClick={onOpenSearch}
        >
          <SearchIcon className="size-3.5" />
        </Button>
        <ChatNavigatorMenu
          mode={mode}
          agentSort={agentSort}
          sessionSort={sessionSort}
          singleExpandedAgent={singleExpandedAgent}
          onModeChange={onModeChange}
          onAgentSortChange={onAgentSortChange}
          onSessionSortChange={onSessionSortChange}
          onSingleExpandedAgentChange={onSingleExpandedAgentChange}
        />
      </span>
    </SidebarGroupLabel>
  )
}
