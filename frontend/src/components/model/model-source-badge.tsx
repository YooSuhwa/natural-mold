'use client'

import { cn } from '@/lib/utils'
import type { ModelSource } from '@/lib/types/model'

interface ModelSourceBadgeProps {
  source: ModelSource | null | undefined
  className?: string
}

/**
 * The badge surfaces *where the metadata came from*, not which runtime
 * library is used to call the model. Catalog sources (LiteLLM, OpenRouter,
 * ...) are external pricing/capability indexes — actual API calls always
 * use the provider's native SDK with the user's bound credential. Showing
 * "LiteLLM" as a label invited the misread that we proxy through LiteLLM,
 * so all catalog-derived rows now share a generic "Catalog" label, with
 * the precise upstream surfaced via the hover tooltip.
 *
 * "Manual" stays distinct because it has user-facing consequence: no
 * pricing or context window data is available.
 */

const CATALOG_CLASSES =
  'bg-sky-100 text-sky-700 ring-sky-200 dark:bg-sky-500/15 dark:text-sky-300 dark:ring-sky-500/30'

const MANUAL_CLASSES = 'bg-muted text-muted-foreground ring-border'

const SOURCE_LABEL: Record<ModelSource, string> = {
  openrouter: 'OpenRouter',
  litellm: 'LiteLLM',
  manual: 'Manual',
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

  const isManual = source === 'manual'
  const label = isManual ? 'Manual' : 'Catalog'
  const tooltip = isManual
    ? 'Custom ID — no pricing or context info'
    : `Pricing & capability metadata from ${SOURCE_LABEL[source]} catalog. Calls use the provider SDK with your credential.`

  return (
    <span
      data-source={source}
      title={tooltip}
      className={cn(
        'inline-flex cursor-help items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        isManual ? MANUAL_CLASSES : CATALOG_CLASSES,
        className,
      )}
    >
      {label}
    </span>
  )
}
