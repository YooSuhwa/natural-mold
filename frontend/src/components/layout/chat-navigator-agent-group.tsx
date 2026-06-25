'use client'

import Link from 'next/link'
import { ChevronRightIcon, MessageSquareIcon, PlusIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import type { ConversationRowActions } from '@/components/chat/use-conversation-row-actions'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ChatNavigatorSessionRow } from '@/components/layout/chat-navigator-session-row'
import { clearChatRouteReplacement } from '@/lib/chat/chat-route-replacement'
import { useConversationPages } from '@/lib/hooks/use-conversations'
import type { AgentSummary, ConversationSort } from '@/lib/types'
import { cn } from '@/lib/utils'

const DEFAULT_SESSION_CAP = 5

interface ChatNavigatorAgentGroupProps {
  agent: AgentSummary
  activeAgentId: string | null
  activeConversationId: string | null
  searchQuery: string
  sessionSort: ConversationSort
  expanded: boolean
  listExpanded: boolean
  shortcutHintsEnabled: boolean
  isSidebarCollapsed?: boolean
  onExpandSidebar?: () => void
  onToggleExpanded: (agentId: string) => void
  onToggleListExpanded: (scope: string) => void
  actions: ConversationRowActions
}

export function agentSessionScope(agentId: string): string {
  return `agent:${agentId}:sessions`
}

export function ChatNavigatorAgentGroup({
  agent,
  activeAgentId,
  activeConversationId,
  searchQuery,
  sessionSort,
  expanded,
  listExpanded,
  shortcutHintsEnabled,
  isSidebarCollapsed: isSidebarCollapsedProp,
  onExpandSidebar,
  onToggleExpanded,
  onToggleListExpanded,
  actions,
}: ChatNavigatorAgentGroupProps) {
  const t = useTranslations('sidebar.agents')
  const isSidebarCollapsed = isSidebarCollapsedProp ?? false
  const isActiveAgent = activeAgentId === agent.id
  // 펼침 판정은 부모의 isAgentExpanded 단일 출처가 내려준 expanded만 따른다
  // (활성 에이전트 기본 펼침 + collapse override도 부모에서 계산됨)
  const shouldExpand = !isSidebarCollapsed && expanded
  const search = searchQuery.trim()
  const {
    data: conversationPages,
    isLoading,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = useConversationPages(
    agent.id,
    {
      limit: 30,
      q: shouldExpand ? search || undefined : undefined,
      sort: sessionSort,
    },
    {
      enabled: shouldExpand,
    },
  )
  const conversations = conversationPages?.pages.flatMap((page) => page.items) ?? []
  const visibleConversations = listExpanded
    ? conversations
    : conversations.slice(0, DEFAULT_SESSION_CAP)
  const hasHiddenRows = conversations.length > DEFAULT_SESSION_CAP
  const showDraft = isActiveAgent && activeConversationId === 'new' && search.length === 0
  const scope = agentSessionScope(agent.id)

  async function handleMore() {
    if (!listExpanded) {
      onToggleListExpanded(scope)
      return
    }
    if (hasNextPage) {
      await fetchNextPage()
      return
    }
    onToggleListExpanded(scope)
  }

  function handleAgentLinkClick() {
    if (isSidebarCollapsed) {
      onExpandSidebar?.()
    }
  }

  return (
    <div className="space-y-0.5">
      <div
        className={cn(
          'group/agent flex min-h-9 items-center gap-1 rounded-lg px-1.5 py-1 text-sm transition-colors hover:bg-sidebar-accent focus-within:bg-sidebar-accent group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:py-0',
          isActiveAgent && 'bg-sidebar-accent text-sidebar-accent-foreground',
        )}
      >
        <button
          type="button"
          onClick={() => onToggleExpanded(agent.id)}
          className="flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted group-data-[collapsible=icon]:hidden"
          aria-label={shouldExpand ? t('collapseAgent') : t('expandAgent')}
          aria-expanded={shouldExpand}
        >
          <ChevronRightIcon
            className={cn('size-3.5 transition-transform', shouldExpand && 'rotate-90')}
          />
        </button>
        <Link
          href={`/agents/${agent.id}`}
          onClick={handleAgentLinkClick}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg outline-hidden focus-visible:ring-2 focus-visible:ring-ring group-data-[collapsible=icon]:size-8 group-data-[collapsible=icon]:min-w-8 group-data-[collapsible=icon]:flex-none group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:gap-0 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:hover:bg-sidebar-accent"
        >
          <AgentAvatar imageUrl={agent.image_url} name={agent.name} size="xs" />
          <span className="truncate font-medium group-data-[collapsible=icon]:sr-only">
            {agent.name}
          </span>
        </Link>
        {agent.unread_count > 0 ? (
          <span className="flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full bg-status-warn px-1 moldy-ui-caption font-semibold text-white group-data-[collapsible=icon]:hidden">
            {agent.unread_count > 99 ? '99+' : agent.unread_count}
          </span>
        ) : null}
        <div
          className={cn(
            'flex shrink-0 items-center gap-0.5 opacity-0 group-hover/agent:opacity-100 group-focus-within/agent:opacity-100 group-data-[collapsible=icon]:hidden',
            isActiveAgent && 'opacity-100',
          )}
        >
          <Button
            variant="ghost"
            size="icon-xs"
            render={
              <Link
                href={`/agents/${agent.id}/conversations/new`}
                onClick={clearChatRouteReplacement}
              />
            }
            aria-label={t('newChatForAgent', { name: agent.name })}
          >
            <PlusIcon className="size-3" />
          </Button>
        </div>
      </div>
      {shouldExpand && agent.description ? (
        <p className="line-clamp-1 px-9 moldy-ui-caption text-muted-foreground">
          {agent.description}
        </p>
      ) : null}
      {shouldExpand ? (
        <div className="space-y-0.5 pl-4">
          {isLoading ? (
            <div className="space-y-1 py-1">
              {Array.from({ length: 2 }).map((_, index) => (
                <Skeleton key={index} className="h-8 w-full" />
              ))}
            </div>
          ) : (
            <>
              {showDraft ? (
                <Link
                  data-chat-session-href={`/agents/${agent.id}/conversations/new`}
                  href={`/agents/${agent.id}/conversations/new`}
                  onClick={clearChatRouteReplacement}
                  className="flex h-9 items-center gap-1.5 rounded-lg bg-primary px-2 text-xs font-medium text-primary-foreground"
                >
                  <MessageSquareIcon className="size-3.5" />
                  <span className="truncate">{t('newConversation')}</span>
                </Link>
              ) : null}
              {visibleConversations.map((conversation, index) => (
                <ChatNavigatorSessionRow
                  key={conversation.id}
                  conversation={conversation}
                  active={activeConversationId === conversation.id}
                  shortcutIndex={shortcutHintsEnabled ? index + 1 : null}
                  actions={actions}
                  isSidebarCollapsed={isSidebarCollapsed}
                  onExpandSidebar={onExpandSidebar}
                />
              ))}
              {conversations.length === 0 && !showDraft ? (
                <p className="px-2 py-2 text-center moldy-ui-caption text-muted-foreground">
                  {search ? t('empty.search') : t('empty.sessions')}
                </p>
              ) : null}
              {hasNextPage || hasHiddenRows ? (
                <Button
                  variant="ghost"
                  size="xs"
                  className="w-full"
                  onClick={handleMore}
                  disabled={isFetchingNextPage}
                >
                  {listExpanded && !hasNextPage ? t('collapse') : t('loadMore')}
                </Button>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  )
}
