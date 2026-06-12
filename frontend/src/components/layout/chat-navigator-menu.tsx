'use client'

import { FolderTreeIcon, ListIcon, MoreHorizontalIcon, UsersIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { AgentSort, ConversationSort, NavigatorMode } from '@/lib/types'

interface ChatNavigatorMenuProps {
  mode: NavigatorMode
  agentSort: AgentSort
  sessionSort: ConversationSort
  singleExpandedAgent: boolean
  onModeChange: (mode: NavigatorMode) => void
  onAgentSortChange: (sort: AgentSort) => void
  onSessionSortChange: (sort: ConversationSort) => void
  onSingleExpandedAgentChange: (enabled: boolean) => void
}

function isNavigatorMode(value: string): value is NavigatorMode {
  return value === 'agent_grouped' || value === 'recent_agents' || value === 'recent_sessions'
}

function isAgentSort(value: string): value is AgentSort {
  return value === 'recent' || value === 'created'
}

function isConversationSort(value: string): value is ConversationSort {
  return value === 'updated' || value === 'created'
}

export function ChatNavigatorMenu({
  mode,
  agentSort,
  sessionSort,
  singleExpandedAgent,
  onModeChange,
  onAgentSortChange,
  onSessionSortChange,
  onSingleExpandedAgentChange,
}: ChatNavigatorMenuProps) {
  const t = useTranslations('sidebar.agents')

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="ghost" size="icon-xs" aria-label={t('menu.label')} />}
        className="opacity-0 group-hover/nav-heading:opacity-100 focus-visible:opacity-100 data-open:opacity-100"
      >
        <MoreHorizontalIcon className="size-3.5" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" side="bottom" sideOffset={4} className="w-56">
        {/* Base UI GroupLabel은 Menu.Group 내부에서만 렌더 가능하다 */}
        <DropdownMenuGroup>
          <DropdownMenuLabel>{t('menu.label')}</DropdownMenuLabel>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <FolderTreeIcon />
              {t('menu.organization')}
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              <DropdownMenuRadioGroup
                value={mode}
                onValueChange={(value) => {
                  if (isNavigatorMode(value)) onModeChange(value)
                }}
              >
                <DropdownMenuRadioItem value="agent_grouped">
                  <FolderTreeIcon />
                  {t('mode.agentGrouped')}
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="recent_agents">
                  <UsersIcon />
                  {t('mode.recentAgents')}
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="recent_sessions">
                  <ListIcon />
                  {t('mode.recentSessions')}
                </DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>{t('menu.agentSort')}</DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              <DropdownMenuRadioGroup
                value={agentSort}
                onValueChange={(value) => {
                  if (isAgentSort(value)) onAgentSortChange(value)
                }}
              >
                <DropdownMenuRadioItem value="recent">{t('sort.recent')}</DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="created">{t('sort.created')}</DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>{t('menu.sessionSort')}</DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              <DropdownMenuRadioGroup
                value={sessionSort}
                onValueChange={(value) => {
                  if (isConversationSort(value)) onSessionSortChange(value)
                }}
              >
                <DropdownMenuRadioItem value="updated">{t('sort.updated')}</DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="created">{t('sort.created')}</DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuCheckboxItem
          checked={singleExpandedAgent}
          onCheckedChange={onSingleExpandedAgentChange}
        >
          {t('menu.singleExpandedAgent')}
        </DropdownMenuCheckboxItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
