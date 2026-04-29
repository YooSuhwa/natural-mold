'use client'

import { cn } from '@/lib/utils'
import type { ModelSource } from '@/lib/types/model'

interface ModelSourceBadgeProps {
  source: ModelSource | null | undefined
  className?: string
}

const STYLES: Record<
  ModelSource,
  { label: string; classes: string }
> = {
  openrouter: {
    label: 'OpenRouter',
    classes:
      'bg-violet-100 text-violet-700 ring-violet-200 dark:bg-violet-500/15 dark:text-violet-300 dark:ring-violet-500/30',
  },
  litellm: {
    label: 'LiteLLM',
    classes:
      'bg-sky-100 text-sky-700 ring-sky-200 dark:bg-sky-500/15 dark:text-sky-300 dark:ring-sky-500/30',
  },
  manual: {
    label: 'Manual',
    classes:
      'bg-muted text-muted-foreground ring-border',
  },
}

export function ModelSourceBadge({ source, className }: ModelSourceBadgeProps) {
  if (!source) {
    return (
      <span
        className={cn(
          'inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground ring-1 ring-inset ring-border',
          className,
        )}
      >
        —
      </span>
    )
  }
  const style = STYLES[source]
  return (
    <span
      data-source={source}
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        style.classes,
        className,
      )}
    >
      {style.label}
    </span>
  )
}
