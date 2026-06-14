'use client'

import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import type { ConversationRowActions } from '@/components/chat/use-conversation-row-actions'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { AgentSummary, ConversationSort, ConversationWithAgent } from '@/lib/types'
import { ChatNavigatorAgentGroup, agentSessionScope } from './chat-navigator-agent-group'
import { ChatNavigatorSessionRow } from './chat-navigator-session-row'
import { RECENT_AGENT_CAP, RECENT_SESSION_CAP } from './chat-navigator-utils'

interface RecentAgentsSectionProps {
  agents: readonly AgentSummary[]
  expanded: boolean
  isSidebarCollapsed?: boolean
  onExpandSidebar?: () => void
  onToggleExpanded: () => void
}

interface RecentSessionsSectionProps {
  conversations: readonly ConversationWithAgent[]
  activeConversationId: string | null
  actions: ConversationRowActions
  expanded: boolean
  hasNextPage: boolean
  isLoading: boolean
  isFetchingNextPage: boolean
  search: string
  isSidebarCollapsed?: boolean
  onExpandSidebar?: () => void
  onMore: () => void | Promise<void>
}

interface AgentGroupedSectionProps {
  agents: readonly AgentSummary[]
  activeAgentId: string | null
  activeConversationId: string | null
  searchQuery: string
  sessionSort: ConversationSort
  isAgentExpanded: (agentId: string) => boolean
  expandedListScopes: readonly string[]
  searchResultSessions: readonly ConversationWithAgent[]
  actions: ConversationRowActions
  isSidebarCollapsed?: boolean
  onExpandSidebar?: () => void
  onToggleAgentExpanded: (agentId: string) => void
  onToggleListExpanded: (scope: string) => void
}

export function ChatNavigatorLoadingRows() {
  return (
    <div className="space-y-1">
      {Array.from({ length: 4 }).map((_, index) => (
        <Skeleton key={index} className="h-9 w-full" />
      ))}
    </div>
  )
}

export function RecentAgentsSection({
  agents,
  expanded,
  isSidebarCollapsed = false,
  onExpandSidebar,
  onToggleExpanded,
}: RecentAgentsSectionProps) {
  const t = useTranslations('sidebar.agents')
  const visibleAgents = expanded ? agents : agents.slice(0, RECENT_AGENT_CAP)

  function handleAgentClick() {
    if (isSidebarCollapsed) {
      onExpandSidebar?.()
    }
  }

  return (
    <div className="space-y-0.5">
      {visibleAgents.map((agent) => (
        <Link
          key={agent.id}
          href={`/agents/${agent.id}`}
          onClick={handleAgentClick}
          className="flex h-9 items-center gap-2 rounded-lg px-2 text-sm transition-colors hover:bg-sidebar-accent group-data-[collapsible=icon]:mx-auto group-data-[collapsible=icon]:size-8 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:gap-0 group-data-[collapsible=icon]:px-0"
        >
          <AgentAvatar imageUrl={agent.image_url} name={agent.name} size="xs" />
          <span className="min-w-0 flex-1 truncate group-data-[collapsible=icon]:sr-only">
            {agent.name}
          </span>
          {agent.unread_count > 0 ? (
            <span className="flex h-4 min-w-4 items-center justify-center rounded-full bg-status-warn px-1 moldy-ui-caption font-semibold text-white group-data-[collapsible=icon]:hidden">
              {agent.unread_count > 99 ? '99+' : agent.unread_count}
            </span>
          ) : null}
        </Link>
      ))}
      {agents.length === 0 ? (
        <p className="px-2 py-2 text-center moldy-ui-caption text-muted-foreground">
          {t('empty.agents')}
        </p>
      ) : null}
      {agents.length > RECENT_AGENT_CAP ? (
        <Button
          variant="ghost"
          size="xs"
          className="w-full group-data-[collapsible=icon]:hidden"
          onClick={onToggleExpanded}
        >
          {expanded ? t('collapse') : t('loadMore')}
        </Button>
      ) : null}
    </div>
  )
}

export function RecentSessionsSection({
  conversations,
  activeConversationId,
  actions,
  expanded,
  hasNextPage,
  isLoading,
  isFetchingNextPage,
  search,
  isSidebarCollapsed = false,
  onExpandSidebar,
  onMore,
}: RecentSessionsSectionProps) {
  const t = useTranslations('sidebar.agents')
  const visibleConversations = expanded ? conversations : conversations.slice(0, RECENT_SESSION_CAP)
  const hasHiddenRows = conversations.length > RECENT_SESSION_CAP

  if (isLoading) return <ChatNavigatorLoadingRows />

  return (
    <div className="space-y-0.5">
      {visibleConversations.map((conversation, index) => (
        <ChatNavigatorSessionRow
          key={conversation.id}
          conversation={conversation}
          agent={conversation.agent}
          active={activeConversationId === conversation.id}
          shortcutIndex={index + 1}
          actions={actions}
          isSidebarCollapsed={isSidebarCollapsed}
          onExpandSidebar={onExpandSidebar}
        />
      ))}
      {visibleConversations.length === 0 ? (
        <p className="px-2 py-2 text-center moldy-ui-caption text-muted-foreground">
          {search ? t('empty.search') : t('empty.sessions')}
        </p>
      ) : null}
      {hasNextPage || hasHiddenRows ? (
        <Button
          variant="ghost"
          size="xs"
          className="w-full group-data-[collapsible=icon]:hidden"
          onClick={onMore}
          disabled={isFetchingNextPage}
        >
          {expanded && !hasNextPage ? t('collapse') : t('loadMore')}
        </Button>
      ) : null}
    </div>
  )
}

export function AgentGroupedSection({
  agents,
  activeAgentId,
  activeConversationId,
  searchQuery,
  sessionSort,
  isAgentExpanded,
  expandedListScopes,
  searchResultSessions,
  actions,
  isSidebarCollapsed = false,
  onExpandSidebar,
  onToggleAgentExpanded,
  onToggleListExpanded,
}: AgentGroupedSectionProps) {
  const t = useTranslations('sidebar.agents')
  const search = searchQuery.trim()
  const hasSearchResultSessions = searchResultSessions.length > 0
  const expandedVisibleAgentIds = agents
    .filter((agent) => isAgentExpanded(agent.id))
    .map((agent) => agent.id)
  const shortcutHintsAgentId =
    !hasSearchResultSessions && expandedVisibleAgentIds.length === 1
      ? expandedVisibleAgentIds[0]
      : null

  return (
    <div className="space-y-1">
      {hasSearchResultSessions ? (
        <div className="space-y-0.5 border-b border-sidebar-border pb-2">
          <p className="px-2 py-1 moldy-ui-caption font-semibold text-muted-foreground">
            {t('searchResults')}
          </p>
          {searchResultSessions.map((conversation, index) => (
            <ChatNavigatorSessionRow
              key={conversation.id}
              conversation={conversation}
              agent={conversation.agent}
              active={activeConversationId === conversation.id}
              shortcutIndex={index + 1}
              actions={actions}
              isSidebarCollapsed={isSidebarCollapsed}
              onExpandSidebar={onExpandSidebar}
            />
          ))}
        </div>
      ) : null}
      {agents.map((agent) => {
        const scope = agentSessionScope(agent.id)
        return (
          <ChatNavigatorAgentGroup
            key={agent.id}
            agent={agent}
            activeAgentId={activeAgentId}
            activeConversationId={activeConversationId}
            searchQuery={searchQuery}
            sessionSort={sessionSort}
            expanded={isAgentExpanded(agent.id)}
            listExpanded={expandedListScopes.includes(scope)}
            shortcutHintsEnabled={shortcutHintsAgentId === agent.id}
            onToggleExpanded={onToggleAgentExpanded}
            onToggleListExpanded={onToggleListExpanded}
            actions={actions}
            isSidebarCollapsed={isSidebarCollapsed}
            onExpandSidebar={onExpandSidebar}
          />
        )
      })}
      {agents.length === 0 && !hasSearchResultSessions ? (
        <p className="px-2 py-2 text-center moldy-ui-caption text-muted-foreground">
          {search ? t('empty.search') : t('empty.agents')}
        </p>
      ) : null}
    </div>
  )
}
