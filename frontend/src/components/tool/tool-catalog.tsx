'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'
import { Skeleton } from '@/components/ui/skeleton'
import { DomainIcon } from '@/components/shared/icon'
import { EmptyState } from '@/components/shared/empty-state'
import {
  ResourceBadge,
  ResourceCardAction,
  ResourceCardDescription,
  ResourceCardMeta,
  ResourceCardTitle,
  ResourceGrid,
  ResourceListCard,
} from '@/components/shared/resource-layout'
import { StatusChip } from '@/components/shared/status-chip'
import { getResourceTone, resourceStatusChipClassName } from '@/lib/resource-tones'
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
        <ResourceGrid minColumnWidth={252}>
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="moldy-skeleton-card h-[152px]" />
          ))}
        </ResourceGrid>
      ) : filtered.length === 0 ? (
        <EmptyState
          title={t('empty.title')}
          description={t('empty.description')}
          className="bg-card/50"
        />
      ) : (
        <ResourceGrid minColumnWidth={252}>
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
      <ResourceGrid minColumnWidth={252}>
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="moldy-skeleton-card h-[176px]" />
        ))}
      </ResourceGrid>
    )
  }

  return (
    <ResourceGrid minColumnWidth={252}>
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
    <ResourceListCard as="button" tone={tone} density="compact" onClick={() => onPick(definition)}>
      <ResourceListCard.Header>
        <span className={cn('moldy-resource-icon', tone.icon)}>
          <DomainIcon iconId={definition.icon_id ?? definition.key} className="size-4.5" />
        </span>
        <ResourceBadge tone={tone}>{categoryLabel}</ResourceBadge>
      </ResourceListCard.Header>

      <ResourceCardTitle>{definition.display_name}</ResourceCardTitle>
      <ResourceCardDescription>{definition.description}</ResourceCardDescription>

      <ResourceListCard.MetaRow>
        {definition.requires_credential ? (
          <ResourceCardMeta>
            <span className="truncate leading-none">{requiresCredentialLabel}</span>
          </ResourceCardMeta>
        ) : null}
      </ResourceListCard.MetaRow>

      <ResourceListCard.Footer>
        <ResourceCardAction>{actionLabel}</ResourceCardAction>
      </ResourceListCard.Footer>
    </ResourceListCard>
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
    <ResourceListCard as="button" tone={tone} density="standard" onClick={() => onOpen(tool)}>
      <ResourceListCard.Header>
        <span className={cn('moldy-resource-icon', tone.icon)}>
          <DomainIcon iconId={definition?.icon_id ?? tool.definition_key} className="size-4.5" />
        </span>
        <ResourceBadge tone={tone}>{categoryLabel}</ResourceBadge>
      </ResourceListCard.Header>

      <ResourceCardTitle>{tool.name}</ResourceCardTitle>
      <ResourceCardDescription>{description}</ResourceCardDescription>

      <ResourceListCard.StatusRow>
        <StatusChip
          variant={tool.enabled ? 'active' : 'disabled'}
          className={resourceStatusChipClassName}
        />
        {tool.credential_id ? (
          <StatusChip
            variant={credentialStatus ?? 'unknown'}
            className={resourceStatusChipClassName}
          />
        ) : definition?.requires_credential ? (
          <ResourceCardMeta>
            <span className="truncate leading-none">{requiresCredentialLabel}</span>
          </ResourceCardMeta>
        ) : null}
      </ResourceListCard.StatusRow>

      <ResourceListCard.Footer>
        <ResourceCardAction>{actionLabel}</ResourceCardAction>
      </ResourceListCard.Footer>
    </ResourceListCard>
  )
}
