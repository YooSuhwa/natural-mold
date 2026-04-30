'use client'

import { useMemo, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import { SearchInput } from '@/components/shared/search-input'
import { DomainIcon } from '@/components/shared/icon'
import { EmptyState } from '@/components/shared/empty-state'
import { useToolTypes } from '@/lib/hooks/use-tools'
import type { ToolDefinition } from '@/lib/types/tool'

interface ToolCatalogProps {
  onPick: (definition: ToolDefinition) => void
}

export function ToolCatalog({ onPick }: ToolCatalogProps) {
  const { data: definitions, isLoading } = useToolTypes()
  const [search, setSearch] = useState('')
  const [activeCategory, setActiveCategory] = useState<string>('all')

  const categories = useMemo(() => {
    const set = new Set<string>()
    definitions?.forEach((d) => set.add(d.category || 'general'))
    return ['all', ...Array.from(set).sort()]
  }, [definitions])

  const filtered = useMemo(() => {
    if (!definitions) return []
    return definitions.filter((d) => {
      if (activeCategory !== 'all' && d.category !== activeCategory) return false
      const q = search.trim().toLowerCase()
      if (!q) return true
      return (
        d.display_name.toLowerCase().includes(q) ||
        d.description.toLowerCase().includes(q) ||
        d.key.toLowerCase().includes(q)
      )
    })
  }, [definitions, activeCategory, search])

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      <aside className="lg:w-48">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Categories
        </p>
        <div className="flex flex-wrap gap-1 lg:flex-col">
          {categories.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setActiveCategory(c)}
              className={`rounded-md px-2 py-1 text-left text-sm transition-colors ${
                activeCategory === c
                  ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300'
                  : 'hover:bg-muted'
              }`}
            >
              {c === 'all' ? 'All' : c}
            </button>
          ))}
        </div>
      </aside>

      <div className="flex-1 space-y-3">
        <SearchInput
          placeholder="Search tools"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />

        {isLoading ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-28 rounded-lg" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState title="No tools match" description="Try a different category or query." />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((d) => (
              <Card
                key={d.key}
                role="button"
                tabIndex={0}
                onClick={() => onPick(d)}
                onKeyDown={(e) => (e.key === 'Enter' ? onPick(d) : null)}
                className="cursor-pointer transition-colors hover:border-primary/40"
              >
                <CardHeader className="pb-2">
                  <div className="flex items-start gap-3">
                    <DomainIcon iconId={d.icon_id ?? d.key} className="size-5 mt-0.5" />
                    <div className="min-w-0 flex-1">
                      <CardTitle className="text-sm">{d.display_name}</CardTitle>
                      <CardDescription className="line-clamp-2 text-xs">
                        {d.description}
                      </CardDescription>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="pt-0 flex flex-wrap gap-1">
                  <Badge variant="secondary" className="text-[10px]">
                    {d.category}
                  </Badge>
                  {d.requires_credential && (
                    <Badge variant="outline" className="text-[10px]">
                      requires credential
                    </Badge>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
