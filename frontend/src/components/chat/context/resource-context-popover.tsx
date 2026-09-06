'use client'

import {
  ComposerPrimitive,
  unstable_useMentionAdapter,
  type Unstable_DirectiveFormatter,
} from '@assistant-ui/react'

import {
  MAX_RESOURCE_CONTEXT_REFERENCES,
  type ResourceContextCandidate,
  type ResourceContextKind,
  type ResourceContextReference,
} from '@/lib/chat/context/resource-context'
import {
  createResourceMentionCategories,
  resourceReferenceFromTriggerMetadata,
} from '@/lib/chat/context/resource-mention-adapter'
import { cn } from '@/lib/utils'
import triggerStyles from './chat-composer-triggers.module.css'

const EMPTY_DIRECTIVE_FORMATTER: Unstable_DirectiveFormatter = {
  serialize: () => '',
  parse: (text) => [{ kind: 'text', text }],
}

export function ResourceContextPopover({
  candidates,
  selectedCount,
  categoryLabels,
  ariaLabel,
  limitReason,
  onSelect,
}: {
  readonly candidates: readonly ResourceContextCandidate[]
  readonly selectedCount: number
  readonly categoryLabels: Readonly<Record<ResourceContextKind, string>>
  readonly ariaLabel: string
  readonly limitReason: string
  readonly onSelect: (reference: ResourceContextReference) => void
}) {
  const atLimit = selectedCount >= MAX_RESOURCE_CONTEXT_REFERENCES
  const mention = unstable_useMentionAdapter({
    categories: createResourceMentionCategories(candidates, categoryLabels),
    includeModelContextTools: false,
    formatter: EMPTY_DIRECTIVE_FORMATTER,
    onInserted: (item) => {
      const reference = resourceReferenceFromTriggerMetadata(item.metadata)
      if (reference && !atLimit) onSelect(reference)
    },
  })

  return (
    <ComposerPrimitive.Unstable_TriggerPopover
      char="@"
      adapter={mention.adapter}
      aria-label={ariaLabel}
      className={cn(
        'moldy-popover mb-2 max-h-72 overflow-y-auto p-1',
        triggerStyles.popoverPosition,
      )}
    >
      <ComposerPrimitive.Unstable_TriggerPopover.Directive {...mention.directive} />
      {atLimit ? <p className="px-2.5 py-2 text-xs text-muted-foreground">{limitReason}</p> : null}
      <ComposerPrimitive.Unstable_TriggerPopoverCategories>
        {(categories) =>
          categories.map((category) => (
            <ComposerPrimitive.Unstable_TriggerPopoverCategoryItem
              key={category.id}
              categoryId={category.id}
              disabled={atLimit}
              className="flex w-full px-2.5 py-2 text-left text-sm font-medium transition-colors hover:bg-accent data-highlighted:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
            >
              {category.label}
            </ComposerPrimitive.Unstable_TriggerPopoverCategoryItem>
          ))
        }
      </ComposerPrimitive.Unstable_TriggerPopoverCategories>
      <ComposerPrimitive.Unstable_TriggerPopoverItems>
        {(items) =>
          items.map((item) => (
            <ComposerPrimitive.Unstable_TriggerPopoverItem
              key={item.id}
              item={item}
              disabled={atLimit}
              className="flex w-full flex-col px-2.5 py-2 text-left text-sm transition-colors hover:bg-accent data-highlighted:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
            >
              <span className="font-medium text-foreground">{item.label}</span>
              {item.description ? (
                <span className="text-xs text-muted-foreground">{item.description}</span>
              ) : null}
            </ComposerPrimitive.Unstable_TriggerPopoverItem>
          ))
        }
      </ComposerPrimitive.Unstable_TriggerPopoverItems>
    </ComposerPrimitive.Unstable_TriggerPopover>
  )
}
