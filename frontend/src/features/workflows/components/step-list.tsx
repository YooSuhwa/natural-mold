'use client'

import { useTranslations } from 'next-intl'

import { cn } from '@/lib/utils'
import type { WorkflowDefinition } from '@/lib/types/workflow'

import { readingOrder } from '../lib/layout'
import { NODE_ICONS } from '../lib/node-catalog'

/** Narrow screens: the same workflow as an ordered list of steps. */
export function StepList({
  definition,
  titleOf,
  issueCounts,
  selectedId,
  onSelect,
}: {
  definition: WorkflowDefinition
  titleOf: (nodeId: string) => { title: string; subtitle: string; detail: string }
  issueCounts: Map<string, number>
  selectedId: string | null
  onSelect: (nodeId: string) => void
}) {
  const t = useTranslations('workflows')
  const byId = new Map(definition.nodes.map((node) => [node.id, node]))
  return (
    <ol className="flex flex-col gap-2" aria-label={t('editor.listView')}>
      {readingOrder(definition).map((id, index) => {
        const node = byId.get(id)
        if (!node) return null
        const Icon = NODE_ICONS[node.type]
        const { title, subtitle, detail } = titleOf(id)
        const issues = issueCounts.get(id) ?? 0
        return (
          <li key={id}>
            <button
              type="button"
              className={cn(
                'moldy-card flex w-full items-center gap-3 px-3 py-2 text-left',
                selectedId === id && 'ring-2 ring-primary-strong',
              )}
              onClick={() => onSelect(id)}
            >
              <span className="w-5 text-xs text-muted-foreground">{index + 1}</span>
              <Icon className="size-4 shrink-0" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{title}</span>
                <span className="block truncate text-xs text-muted-foreground">{subtitle}</span>
                {detail ? (
                  <span className="block truncate font-mono text-xs text-muted-foreground">
                    {detail}
                  </span>
                ) : null}
              </span>
              {issues > 0 ? (
                <span className="moldy-status-surface moldy-status-danger rounded-full px-2 py-0.5 text-xs">
                  {issues}
                </span>
              ) : null}
            </button>
          </li>
        )
      })}
    </ol>
  )
}
