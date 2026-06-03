'use client'

import { useMemo } from 'react'
import { ChevronRightIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Skeleton } from '@/components/ui/skeleton'
import { DomainIcon } from '@/components/shared/icon'
import { EmptyState } from '@/components/shared/empty-state'
import { ResourceGrid } from '@/components/shared/resource-layout'
import { StatusChip } from '@/components/shared/status-chip'
import {
  getResourceTone,
  resourceCardClassName,
  resourceMetaClassName,
  type ResourceTone,
} from '@/lib/resource-tones'
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
            <Skeleton key={i} className="h-[152px] rounded-xl" />
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
          <Skeleton key={i} className="h-[152px] rounded-xl" />
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
  const tone = getResourceTone(definition.category || 'general')

  return (
    <button type="button" onClick={() => onPick(definition)} className={toolCardClassName(tone)}>
      <div className="flex items-start justify-between gap-3">
        <span
          className={cn(
            'inline-flex size-9 shrink-0 items-center justify-center rounded-xl ring-1',
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
  const tone = getResourceTone(category)
  const description = tool.description || definition?.description || ''

  return (
    <button type="button" onClick={() => onOpen(tool)} className={toolCardClassName(tone)}>
      <div className="flex items-start justify-between gap-3">
        <span
          className={cn(
            'inline-flex size-9 shrink-0 items-center justify-center rounded-xl ring-1',
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
          <span className={resourceMetaClassName}>
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

function toolCardClassName(tone: ResourceTone): string {
  return resourceCardClassName(tone, 'min-h-[152px]')
}
