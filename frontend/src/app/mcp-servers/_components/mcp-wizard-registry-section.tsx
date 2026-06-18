'use client'

import { useTranslations } from 'next-intl'

import { DomainIconTile } from '@/components/shared/icon'
import { Button } from '@/components/ui/button'
import type { McpRegistryEntry } from '@/lib/types/mcp'

type McpWizardRegistrySectionProps = {
  readonly entries: readonly McpRegistryEntry[]
  readonly selected: string | null
  readonly onSelect: (entry: McpRegistryEntry) => void
  readonly onClear: () => void
}

export function McpWizardRegistrySection({
  entries,
  selected,
  onSelect,
  onClear,
}: McpWizardRegistrySectionProps) {
  const t = useTranslations('mcp.wizard.registry')
  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t('title')}
        </h3>
        {selected ? (
          <Button size="sm" variant="ghost" onClick={onClear}>
            {t('clear')}
          </Button>
        ) : null}
      </div>
      {entries.length === 0 ? (
        <p className="rounded border border-dashed border-border/60 p-4 text-center text-xs text-muted-foreground">
          {t('empty')}
        </p>
      ) : (
        <div role="list" className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {entries.map((entry) => {
            const isSelected = selected === entry.key
            return (
              <button
                key={entry.key}
                type="button"
                role="listitem"
                onClick={() => onSelect(entry)}
                data-testid={`registry-card-${entry.key}`}
                className={`flex items-start gap-2.5 rounded-lg border p-2.5 text-left transition-[background-color,border-color,box-shadow] hover:bg-muted/50 ${
                  isSelected ? 'moldy-selected-card' : 'border-border'
                }`}
              >
                <DomainIconTile
                  iconId={entry.icon_id ?? 'server'}
                  className="size-9"
                  iconClassName="size-5"
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{entry.display_name}</p>
                  {entry.description ? (
                    <p className="line-clamp-1 moldy-ui-caption text-muted-foreground">
                      {entry.description}
                    </p>
                  ) : null}
                  <p className="mt-1 moldy-ui-micro uppercase tracking-wide text-muted-foreground">
                    {entry.transport}
                  </p>
                </div>
              </button>
            )
          })}
        </div>
      )}
    </section>
  )
}
