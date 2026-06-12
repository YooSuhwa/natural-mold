'use client'

import { useDeferredValue, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { BotIcon, MessageSquareIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { DialogShell } from '@/components/shared/dialog-shell'
import { SearchInput } from '@/components/shared/search-input'
import { Button } from '@/components/ui/button'
import { useGlobalConversationPages } from '@/lib/hooks/use-conversations'
import type { AgentSummary } from '@/lib/types'

interface ChatQuickSwitcherProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  agents: readonly AgentSummary[]
}

function matchesAgent(agent: AgentSummary, query: string): boolean {
  const normalized = query.toLowerCase()
  return (
    agent.name.toLowerCase().includes(normalized) ||
    (agent.description ?? '').toLowerCase().includes(normalized)
  )
}

export function ChatQuickSwitcher({ open, onOpenChange, agents }: ChatQuickSwitcherProps) {
  const router = useRouter()
  const t = useTranslations('sidebar.agents.quickSwitcher')
  const [query, setQuery] = useState('')
  const trimmedQuery = query.trim()
  // 서버 검색은 deferred 값으로 — 키스트로크마다 fetch가 나가지 않게 한다
  const deferredQuery = useDeferredValue(trimmedQuery)
  const filteredAgents = useMemo(
    () =>
      agents
        .filter((agent) => (trimmedQuery ? matchesAgent(agent, trimmedQuery) : true))
        .slice(0, 6),
    [agents, trimmedQuery],
  )
  const { data: globalPages } = useGlobalConversationPages(
    {
      limit: 8,
      q: deferredQuery || undefined,
      sort: 'updated',
    },
    {
      enabled: open,
    },
  )
  const sessions = globalPages?.pages.flatMap((page) => page.items).slice(0, 8) ?? []

  function navigateTo(href: string) {
    onOpenChange(false)
    router.push(href)
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="md" height="auto">
      <DialogShell.Header title={t('title')} description={t('description')} />
      <DialogShell.Body>
        <div className="space-y-3">
          <SearchInput
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('placeholder')}
            aria-label={t('placeholder')}
            autoFocus
          />
          <div className="space-y-2">
            <p className="moldy-ui-caption font-semibold text-muted-foreground">{t('agents')}</p>
            <div className="space-y-1">
              {filteredAgents.map((agent) => (
                <Button
                  key={agent.id}
                  variant="ghost"
                  className="w-full justify-start"
                  onClick={() => navigateTo(`/agents/${agent.id}`)}
                >
                  <AgentAvatar imageUrl={agent.image_url} name={agent.name} size="xs" />
                  <span className="truncate">{agent.name}</span>
                </Button>
              ))}
              {filteredAgents.length === 0 ? (
                <p className="px-2 py-1 moldy-ui-caption text-muted-foreground">
                  {t('emptyAgents')}
                </p>
              ) : null}
            </div>
          </div>
          <div className="space-y-2">
            <p className="moldy-ui-caption font-semibold text-muted-foreground">{t('sessions')}</p>
            <div className="space-y-1">
              {sessions.map((conversation) => (
                <Button
                  key={conversation.id}
                  variant="ghost"
                  className="w-full justify-start"
                  onClick={() =>
                    navigateTo(`/agents/${conversation.agent_id}/conversations/${conversation.id}`)
                  }
                >
                  <MessageSquareIcon className="size-4" />
                  <span className="min-w-0 flex-1 truncate text-left">
                    {conversation.title ?? t('fallbackTitle')}
                  </span>
                  <span className="inline-flex items-center gap-1 moldy-ui-caption text-muted-foreground">
                    <BotIcon className="size-3" />
                    {conversation.agent.name}
                  </span>
                </Button>
              ))}
              {sessions.length === 0 ? (
                <p className="px-2 py-1 moldy-ui-caption text-muted-foreground">
                  {t('emptySessions')}
                </p>
              ) : null}
            </div>
          </div>
        </div>
      </DialogShell.Body>
    </DialogShell>
  )
}
