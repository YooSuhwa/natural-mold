'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { PlusIcon, SearchIcon, Trash2Icon, UsersIcon } from 'lucide-react'
import { useAgents } from '@/lib/hooks/use-agents'
import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import type { Agent } from '@/lib/types'

interface SubAgentsDialogProps {
  open: boolean
  onOpenChange: (v: boolean) => void
  selectedSubAgentIds: Set<string>
  onToggleSubAgent: (id: string) => void
  /** 자기 자신 제외용. 매뉴얼 페이지는 빈 문자열 전달. */
  currentAgentId: string
}

export function SubAgentsDialog({
  open,
  onOpenChange,
  selectedSubAgentIds,
  onToggleSubAgent,
  currentAgentId,
}: SubAgentsDialogProps) {
  const t = useTranslations('agent.settings.subAgents')
  const [query, setQuery] = useState('')
  const { data: agents } = useAgents()

  const candidates = useMemo(
    () => agents?.filter((a) => a.id !== currentAgentId) ?? [],
    [agents, currentAgentId],
  )
  const selected = useMemo(
    () => candidates.filter((a) => selectedSubAgentIds.has(a.id)),
    [candidates, selectedSubAgentIds],
  )
  const available = useMemo(() => {
    const q = query.trim().toLowerCase()
    return candidates
      .filter((a) => !selectedSubAgentIds.has(a.id))
      .filter((a) => {
        if (!q) return true
        return (
          a.name.toLowerCase().includes(q) ||
          (a.description ?? '').toLowerCase().includes(q)
        )
      })
  }, [candidates, selectedSubAgentIds, query])

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="xl" height="tall">
      <DialogShell.Header
        icon={<UsersIcon className="size-5" />}
        title={t('manageTitle')}
        description={t('manageDescription')}
      />
      <DialogShell.Body>
        <div className="grid gap-6 md:grid-cols-2">
          <CurrentColumn
            isLoading={!agents}
            selected={selected}
            onRemove={onToggleSubAgent}
          />
          <AvailableColumn
            isLoading={!agents}
            available={available}
            query={query}
            onQueryChange={setQuery}
            onAdd={onToggleSubAgent}
          />
        </div>
      </DialogShell.Body>
    </DialogShell>
  )
}

function CurrentColumn({
  isLoading,
  selected,
  onRemove,
}: {
  isLoading: boolean
  selected: Agent[]
  onRemove: (id: string) => void
}) {
  const t = useTranslations('agent.settings.subAgents')

  return (
    <section className="flex min-h-0 flex-col">
      <h3 className="mb-3 text-sm font-medium">
        {t('currentLabel')} ({selected.length})
      </h3>
      <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1 sm:h-[60vh]">
        {isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : selected.length === 0 ? (
          <EmptyBox>{t('placeholder')}</EmptyBox>
        ) : (
          selected.map((agent) => (
            <SelectedCard key={agent.id} agent={agent} onRemove={() => onRemove(agent.id)} />
          ))
        )}
      </div>
    </section>
  )
}

function AvailableColumn({
  isLoading,
  available,
  query,
  onQueryChange,
  onAdd,
}: {
  isLoading: boolean
  available: Agent[]
  query: string
  onQueryChange: (v: string) => void
  onAdd: (id: string) => void
}) {
  const t = useTranslations('agent.settings.subAgents')

  return (
    <section className="flex min-h-0 flex-col">
      <h3 className="mb-3 text-sm font-medium">{t('availableLabel')}</h3>
      <div className="relative mb-3">
        <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder={t('searchPlaceholder')}
          aria-label={t('searchPlaceholder')}
          className="pl-9 focus-visible:border-input focus-visible:ring-0"
        />
      </div>
      <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1 sm:h-[60vh]">
        {isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : available.length === 0 ? (
          <AvailableEmpty hasQuery={query.trim().length > 0} />
        ) : (
          available.map((agent) => (
            <AvailableCard key={agent.id} agent={agent} onAdd={() => onAdd(agent.id)} />
          ))
        )}
      </div>
    </section>
  )
}

function SelectedCard({ agent, onRemove }: { agent: Agent; onRemove: () => void }) {
  const t = useTranslations('agent.settings.subAgents')
  return (
    <div className="flex items-start gap-3 rounded-lg border p-3">
      <AgentAvatar imageUrl={agent.image_url ?? null} name={agent.name} size="sm" />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm font-medium">{agent.name}</span>
        {agent.description && (
          <span className="line-clamp-1 text-xs text-muted-foreground">
            {agent.description}
          </span>
        )}
      </div>
      <Button
        size="icon-sm"
        variant="ghost"
        onClick={onRemove}
        aria-label={`${t('remove')} ${agent.name}`}
        className="shrink-0"
      >
        <Trash2Icon className="size-4" />
      </Button>
    </div>
  )
}

function AvailableCard({ agent, onAdd }: { agent: Agent; onAdd: () => void }) {
  const t = useTranslations('agent.settings.subAgents')
  return (
    <div className="flex items-start gap-3 rounded-lg border p-3">
      <AgentAvatar imageUrl={agent.image_url ?? null} name={agent.name} size="sm" />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm font-medium">{agent.name}</span>
        {agent.description && (
          <span className="line-clamp-1 text-xs text-muted-foreground">
            {agent.description}
          </span>
        )}
        {agent.model && (
          <span className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground/80">
            {agent.model.display_name}
          </span>
        )}
      </div>
      <Button
        size="sm"
        variant="outline"
        onClick={onAdd}
        aria-label={`${t('addAction')} ${agent.name}`}
        className="shrink-0"
      >
        <PlusIcon className="size-3.5" />
        {t('addAction')}
      </Button>
    </div>
  )
}

function EmptyBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-32 items-center justify-center rounded-lg border border-dashed text-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}

function AvailableEmpty({ hasQuery }: { hasQuery: boolean }) {
  const t = useTranslations('agent.settings.subAgents')
  if (hasQuery) return <EmptyBox>{t('searchEmpty')}</EmptyBox>

  const emptyHintParts = String(t.raw('emptyHint')).split('{link}')
  return (
    <div className="flex h-32 flex-col items-center justify-center gap-1 rounded-lg border border-dashed text-center text-sm text-muted-foreground">
      <p>{t('empty')}</p>
      {emptyHintParts.length === 2 && (
        <p>
          {emptyHintParts[0]}
          <Link href="/" className="text-primary-strong hover:underline">
            {t('dashboardLink')}
          </Link>
          {emptyHintParts[1]}
        </p>
      )}
    </div>
  )
}
