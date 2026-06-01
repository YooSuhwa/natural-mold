'use client'

import { useMemo } from 'react'
import { ChevronRightIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Skeleton } from '@/components/ui/skeleton'
import { DomainIcon } from '@/components/shared/icon'
import { EmptyState } from '@/components/shared/empty-state'
import { ResourceGrid } from '@/components/shared/resource-layout'
import { StatusChip } from '@/components/shared/status-chip'
import { cn } from '@/lib/utils'
import type { ToolDefinition, ToolInstance } from '@/lib/types/tool'

interface ToolCatalogProps {
  category: string
  definitions: ToolDefinition[] | undefined
  isLoading: boolean
  search: string
  onPick: (definition: ToolDefinition) => void
}

interface InstalledToolCatalogProps {
  tools: ToolInstance[]
  definitions: ToolDefinition[] | undefined
  credentialStatuses: Map<string, string>
  isLoading: boolean
  onOpen: (tool: ToolInstance) => void
}

type ToolCardTone = {
  card: string
  icon: string
  badge: string
  dot: string
}

const TOOL_CARD_TONES: ToolCardTone[] = [
  {
    card: 'bg-violet-50/75 hover:border-violet-200 dark:bg-violet-500/10 dark:hover:border-violet-400/30',
    icon: 'bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-200',
    badge:
      'border-violet-100 bg-white/70 text-violet-800 dark:border-violet-400/20 dark:bg-violet-500/10 dark:text-violet-200',
    dot: 'bg-violet-500',
  },
  {
    card: 'bg-sky-50/75 hover:border-sky-200 dark:bg-sky-500/10 dark:hover:border-sky-400/30',
    icon: 'bg-sky-100 text-sky-700 dark:bg-sky-500/20 dark:text-sky-200',
    badge:
      'border-sky-100 bg-white/70 text-sky-800 dark:border-sky-400/20 dark:bg-sky-500/10 dark:text-sky-200',
    dot: 'bg-sky-500',
  },
  {
    card: 'bg-emerald-50/75 hover:border-emerald-200 dark:bg-emerald-500/10 dark:hover:border-emerald-400/30',
    icon: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-200',
    badge:
      'border-emerald-100 bg-white/70 text-emerald-800 dark:border-emerald-400/20 dark:bg-emerald-500/10 dark:text-emerald-200',
    dot: 'bg-emerald-500',
  },
  {
    card: 'bg-amber-50/75 hover:border-amber-200 dark:bg-amber-500/10 dark:hover:border-amber-400/30',
    icon: 'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-200',
    badge:
      'border-amber-100 bg-white/70 text-amber-800 dark:border-amber-400/20 dark:bg-amber-500/10 dark:text-amber-200',
    dot: 'bg-amber-500',
  },
  {
    card: 'bg-rose-50/75 hover:border-rose-200 dark:bg-rose-500/10 dark:hover:border-rose-400/30',
    icon: 'bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-200',
    badge:
      'border-rose-100 bg-white/70 text-rose-800 dark:border-rose-400/20 dark:bg-rose-500/10 dark:text-rose-200',
    dot: 'bg-rose-500',
  },
]

export function ToolCatalog({
  category,
  definitions,
  isLoading,
  search,
  onPick,
}: ToolCatalogProps) {
  const t = useTranslations('tool.catalog')

  const filtered = useMemo(() => {
    if (!definitions) return []
    return definitions.filter((d) => {
      if (category !== 'all' && (d.category || 'general') !== category) return false
      const q = search.trim().toLowerCase()
      if (!q) return true
      return (
        d.display_name.toLowerCase().includes(q) ||
        d.description.toLowerCase().includes(q) ||
        d.key.toLowerCase().includes(q)
      )
    })
  }, [definitions, category, search])

  function formatCategoryLabel(category: string): string {
    if (
      category === 'all' ||
      category === 'general' ||
      category === 'search' ||
      category === 'productivity' ||
      category === 'communication' ||
      category === 'automation'
    ) {
      return t(`categories.${category}`)
    }
    return category
  }

  return (
    <div className="min-h-0">
      {isLoading ? (
        <ResourceGrid minColumnWidth={240}>
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-[152px] rounded-md" />
          ))}
        </ResourceGrid>
      ) : filtered.length === 0 ? (
        <EmptyState
          title={t('empty.title')}
          description={t('empty.description')}
          className="bg-card/50"
        />
      ) : (
        <ResourceGrid minColumnWidth={240}>
          {filtered.map((definition) => (
            <ToolDefinitionCard
              key={definition.key}
              definition={definition}
              categoryLabel={formatCategoryLabel(definition.category || 'general')}
              requiresCredentialLabel={t('requiresCredential')}
              actionLabel={t('selectAction')}
              onPick={onPick}
            />
          ))}
        </ResourceGrid>
      )}
    </div>
  )
}

export function InstalledToolCatalog({
  tools,
  definitions,
  credentialStatuses,
  isLoading,
  onOpen,
}: InstalledToolCatalogProps) {
  const t = useTranslations('tool.catalog')

  const definitionMap = useMemo(() => {
    const map = new Map<string, ToolDefinition>()
    definitions?.forEach((definition) => map.set(definition.key, definition))
    return map
  }, [definitions])

  function formatCategoryLabel(category: string): string {
    if (
      category === 'all' ||
      category === 'general' ||
      category === 'search' ||
      category === 'productivity' ||
      category === 'communication' ||
      category === 'automation'
    ) {
      return t(`categories.${category}`)
    }
    return category
  }

  if (isLoading) {
    return (
      <ResourceGrid minColumnWidth={240}>
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-[152px] rounded-md" />
        ))}
      </ResourceGrid>
    )
  }

  return (
    <ResourceGrid minColumnWidth={240}>
      {tools.map((tool) => {
        const definition = definitionMap.get(tool.definition_key)
        const category = definition?.category || 'general'
        return (
          <InstalledToolCard
            key={tool.id}
            tool={tool}
            definition={definition}
            categoryLabel={formatCategoryLabel(category)}
            requiresCredentialLabel={t('requiresCredential')}
            actionLabel={t('manageAction')}
            credentialStatus={
              tool.credential_id ? credentialStatuses.get(tool.credential_id) : undefined
            }
            onOpen={onOpen}
          />
        )
      })}
    </ResourceGrid>
  )
}

function ToolDefinitionCard({
  definition,
  categoryLabel,
  requiresCredentialLabel,
  actionLabel,
  onPick,
}: {
  definition: ToolDefinition
  categoryLabel: string
  requiresCredentialLabel: string
  actionLabel: string
  onPick: (definition: ToolDefinition) => void
}) {
  const tone = pickToolCardTone(definition)

  return (
    <button type="button" onClick={() => onPick(definition)} className={toolCardClassName(tone)}>
      <div className="flex items-start justify-between gap-3">
        <span
          className={cn(
            'inline-flex size-9 shrink-0 items-center justify-center rounded-lg',
            tone.icon,
          )}
        >
          <DomainIcon iconId={definition.icon_id ?? definition.key} className="size-4.5" />
        </span>
        <span
          className={cn(
            'inline-flex min-w-0 max-w-[120px] items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-semibold leading-none',
            tone.badge,
          )}
        >
          <span className={cn('size-1.5 shrink-0 rounded-full', tone.dot)} />
          <span className="truncate">{categoryLabel}</span>
        </span>
      </div>

      <span className="mt-3 line-clamp-1 text-[15px] font-bold leading-tight text-foreground">
        {definition.display_name}
      </span>
      <p className="mt-2 line-clamp-2 min-h-[2.65em] text-xs leading-[1.45] text-muted-foreground">
        {definition.description}
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {definition.requires_credential ? (
          <span className="inline-flex max-w-[128px] items-center rounded border border-white/80 bg-white/55 px-1.5 py-0.5 text-[10.5px] font-semibold text-foreground shadow-sm dark:border-white/10 dark:bg-white/10">
            <span className="truncate leading-none">{requiresCredentialLabel}</span>
          </span>
        ) : null}
      </div>

      <div className="mt-auto flex items-center justify-end pt-3">
        <span
          className={cn(
            'inline-flex items-center gap-0.5 text-xs font-semibold text-muted-foreground transition-all duration-150',
            'group-hover:translate-x-0.5 group-hover:text-[var(--primary-strong)]',
            'group-focus-visible:translate-x-0.5 group-focus-visible:text-[var(--primary-strong)]',
          )}
        >
          {actionLabel}
          <ChevronRightIcon aria-hidden className="size-3" />
        </span>
      </div>
    </button>
  )
}

function InstalledToolCard({
  tool,
  definition,
  categoryLabel,
  requiresCredentialLabel,
  actionLabel,
  credentialStatus,
  onOpen,
}: {
  tool: ToolInstance
  definition: ToolDefinition | undefined
  categoryLabel: string
  requiresCredentialLabel: string
  actionLabel: string
  credentialStatus: string | undefined
  onOpen: (tool: ToolInstance) => void
}) {
  const category = definition?.category || 'general'
  const tone = pickToolCardTone(`${category}:${tool.definition_key}:${tool.name}`)
  const description = tool.description || definition?.description || ''

  return (
    <button type="button" onClick={() => onOpen(tool)} className={toolCardClassName(tone)}>
      <div className="flex items-start justify-between gap-3">
        <span
          className={cn(
            'inline-flex size-9 shrink-0 items-center justify-center rounded-lg',
            tone.icon,
          )}
        >
          <DomainIcon iconId={definition?.icon_id ?? tool.definition_key} className="size-4.5" />
        </span>
        <span
          className={cn(
            'inline-flex min-w-0 max-w-[120px] items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-semibold leading-none',
            tone.badge,
          )}
        >
          <span className={cn('size-1.5 shrink-0 rounded-full', tone.dot)} />
          <span className="truncate">{categoryLabel}</span>
        </span>
      </div>

      <span className="mt-3 line-clamp-1 text-[15px] font-bold leading-tight text-foreground">
        {tool.name}
      </span>
      <p className="mt-2 line-clamp-2 min-h-[2.65em] text-xs leading-[1.45] text-muted-foreground">
        {description}
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <StatusChip
          variant={tool.enabled ? 'active' : 'disabled'}
          className="max-w-[128px] bg-white/55 text-[10.5px] shadow-sm ring-white/80 dark:bg-white/10 dark:ring-white/10"
        />
        {tool.credential_id ? (
          <StatusChip
            variant={credentialStatus ?? 'unknown'}
            className="max-w-[128px] bg-white/55 text-[10.5px] shadow-sm ring-white/80 dark:bg-white/10 dark:ring-white/10"
          />
        ) : definition?.requires_credential ? (
          <span className="inline-flex max-w-[128px] items-center rounded border border-white/80 bg-white/55 px-1.5 py-0.5 text-[10.5px] font-semibold text-foreground shadow-sm dark:border-white/10 dark:bg-white/10">
            <span className="truncate leading-none">{requiresCredentialLabel}</span>
          </span>
        ) : null}
      </div>

      <div className="mt-auto flex items-center justify-end pt-3">
        <span
          className={cn(
            'inline-flex items-center gap-0.5 text-xs font-semibold text-muted-foreground transition-all duration-150',
            'group-hover:translate-x-0.5 group-hover:text-[var(--primary-strong)]',
            'group-focus-visible:translate-x-0.5 group-focus-visible:text-[var(--primary-strong)]',
          )}
        >
          {actionLabel}
          <ChevronRightIcon aria-hidden className="size-3" />
        </span>
      </div>
    </button>
  )
}

function toolCardClassName(tone: ToolCardTone): string {
  return cn(
    'group relative flex min-h-[152px] flex-col rounded-md border border-transparent p-4 text-left',
    'shadow-[0_10px_24px_-22px_rgba(15,23,42,0.45)] transition-all duration-150',
    'hover:-translate-y-px hover:shadow-[0_18px_32px_-24px_rgba(15,23,42,0.55)]',
    'focus-visible:-translate-y-px focus-visible:border-emerald-300 focus-visible:shadow-md',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
    tone.card,
  )
}

function pickToolCardTone(seed: string | ToolDefinition): ToolCardTone {
  if (typeof seed !== 'string') {
    seed = `${seed.category}:${seed.key}:${seed.display_name}`
  }
  let hash = 0
  for (let i = 0; i < seed.length; i += 1) hash += seed.charCodeAt(i)
  return TOOL_CARD_TONES[hash % TOOL_CARD_TONES.length]
}
