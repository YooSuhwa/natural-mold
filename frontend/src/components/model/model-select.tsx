'use client'

import { useState, useMemo } from 'react'
import { SearchIcon, CheckIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Badge } from '@/components/ui/badge'
import { useModels } from '@/lib/hooks/use-models'
import { Skeleton } from '@/components/ui/skeleton'
import { getProviderIcon, formatContextWindow } from '@/lib/utils/provider'
import type { Model } from '@/lib/types'

interface ModelSelectProps {
  value: string
  onValueChange: (value: string) => void
  className?: string
}

export function ModelSelect({ value, onValueChange, className }: ModelSelectProps) {
  const { data: models, isLoading } = useModels()
  const t = useTranslations('agent.settings')
  const tm = useTranslations('model')
  const [search, setSearch] = useState('')

  const filteredModels = useMemo(() => {
    if (!models) return []
    const q = search.toLowerCase()
    return q
      ? models.filter(
          (m) =>
            m.display_name.toLowerCase().includes(q) ||
            m.model_name.toLowerCase().includes(q) ||
            m.provider.toLowerCase().includes(q),
        )
      : models
  }, [models, search])

  if (isLoading) return <Skeleton className="h-[200px] w-full" />

  return (
    <div className={className}>
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
        <input
          className="h-7 flex-1 border-0 bg-transparent text-sm shadow-none outline-none ring-0 placeholder:text-muted-foreground focus:outline-none focus:ring-0"
          placeholder={tm('searchModels')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="max-h-[240px] overflow-auto p-1.5" role="listbox">
        {filteredModels.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">{t('modelPlaceholder')}</p>
        ) : (
          filteredModels.map((model) => (
            <ModelSelectItem
              key={model.id}
              model={model}
              selected={value === model.id}
              onSelect={() => onValueChange(model.id)}
            />
          ))
        )}
      </div>
    </div>
  )
}

function ModelSelectItem({
  model,
  selected,
  onSelect,
}: {
  model: Model
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      onClick={onSelect}
      className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm ${
        selected ? 'bg-muted/60' : 'hover:bg-muted/30'
      }`}
    >
      {selected ? (
        <CheckIcon className="size-3.5 shrink-0 text-primary" />
      ) : (
        <span className="size-3.5 shrink-0" />
      )}
      <div className="flex size-5 items-center justify-center rounded bg-muted text-[8px] font-bold text-muted-foreground">
        {getProviderIcon(model.provider)}
      </div>
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className="truncate text-xs font-medium">{model.display_name}</span>
        {model.context_window && (
          <Badge variant="outline" className="shrink-0 text-[10px]">
            {formatContextWindow(model.context_window)}
          </Badge>
        )}
        {model.input_modalities?.map((m) => (
          <Badge key={m} variant="secondary" className="shrink-0 text-[10px]">
            {m}
          </Badge>
        ))}
      </div>
    </button>
  )
}
