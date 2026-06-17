'use client'

import type { ReactNode } from 'react'
import { PlusIcon, SearchIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

import type { AvailableKind } from './tools-skills-dialog-types'
import { KindIcon } from './tools-skills-kind-icon'

export function AvailableList({
  query,
  onQueryChange,
  items,
  emptyText,
}: {
  readonly query: string
  readonly onQueryChange: (value: string) => void
  readonly items: readonly ReactNode[]
  readonly emptyText: string
}) {
  return (
    <div className="space-y-3">
      <SearchBar value={query} onChange={onQueryChange} />
      <div className="space-y-2">
        {items.length === 0 ? <EmptyBox>{emptyText}</EmptyBox> : items}
      </div>
    </div>
  )
}

export function AvailableRow({
  kind,
  name,
  subtitle,
  description,
  quality,
  onAdd,
}: {
  readonly kind: AvailableKind
  readonly name: string
  readonly subtitle: ReactNode
  readonly description?: string | null
  readonly quality?: ReactNode
  readonly onAdd: () => void
}) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')

  return (
    <div className="flex items-start gap-3 rounded-lg border p-3">
      <KindIcon kind={kind} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{name}</p>
        <p className="truncate moldy-ui-caption text-muted-foreground">{subtitle}</p>
        {quality ? <div className="mt-1 flex flex-wrap gap-1">{quality}</div> : null}
        {description ? (
          <p className="mt-0.5 line-clamp-2 moldy-ui-caption text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      <Button
        size="sm"
        variant="outline"
        onClick={onAdd}
        className="shrink-0"
        aria-label={t('addNamed', { name })}
      >
        <PlusIcon className="size-3.5" />
        {t('add')}
      </Button>
    </div>
  )
}

export function EmptyBox({ children }: { readonly children: ReactNode }) {
  return (
    <div className="flex h-32 items-center justify-center rounded-lg border border-dashed text-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}

function SearchBar({
  value,
  onChange,
}: {
  readonly value: string
  readonly onChange: (value: string) => void
}) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')

  return (
    <div className="relative">
      <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={t('search')}
        className="pl-9 focus-visible:border-input focus-visible:ring-0"
      />
    </div>
  )
}
