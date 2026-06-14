'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { PlusIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useCredentialTypes } from '@/lib/hooks/use-credentials'

import type { CatalogDefinition } from './tools-skills-dialog-types'
import { KindIcon } from './tools-skills-kind-icon'
import { AvailableList } from './tools-skills-list'

export function CatalogPanel() {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  const { data: definitions } = useCredentialTypes()
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => {
    const all = (definitions ?? []).filter((definition) => definition.category !== 'llm')
    const normalizedQuery = query.trim().toLowerCase()
    if (!normalizedQuery) return all
    return all.filter(
      (definition) =>
        definition.display_name.toLowerCase().includes(normalizedQuery) ||
        definition.key.toLowerCase().includes(normalizedQuery) ||
        (definition.category ?? '').toLowerCase().includes(normalizedQuery),
    )
  }, [definitions, query])

  if (!definitions) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }

  return (
    <AvailableList
      query={query}
      onQueryChange={setQuery}
      items={filtered.map((definition) => (
        <CatalogRow key={definition.key} definition={definition} />
      ))}
      emptyText={t('catalogEmpty')}
    />
  )
}

function CatalogRow({ definition }: { readonly definition: CatalogDefinition }) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')

  return (
    <div className="flex items-start gap-3 rounded-lg border p-3">
      <KindIcon kind="catalog" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium">{definition.display_name}</p>
          <Badge variant="secondary" className="shrink-0 moldy-ui-micro">
            {definition.category}
          </Badge>
        </div>
        <p className="truncate font-mono moldy-ui-caption text-muted-foreground">
          {definition.key}
        </p>
      </div>
      <Button
        size="sm"
        variant="outline"
        className="shrink-0"
        render={
          <Link
            href={`/tools?create=${encodeURIComponent(definition.key)}`}
            aria-label={t('createNamed', { name: definition.display_name })}
          />
        }
      >
        <PlusIcon className="size-3.5" />
        {t('create')}
      </Button>
    </div>
  )
}
