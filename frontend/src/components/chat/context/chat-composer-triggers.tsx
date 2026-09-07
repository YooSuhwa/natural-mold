'use client'

import { ComposerPrimitive } from '@assistant-ui/react'
import type { ReactNode } from 'react'

import { ChatCommandPopover } from '@/components/chat/commands/chat-command-popover'
import { ResourceContextPopover } from '@/components/chat/context/resource-context-popover'
import type { ChatCommandDefinition } from '@/lib/chat/commands/chat-command-types'
import type {
  ResourceContextCandidate,
  ResourceContextKind,
  ResourceContextReference,
} from '@/lib/chat/context/resource-context'

export function ChatComposerTriggers({
  children,
  commands,
  commandAriaLabel,
  resourceCandidates,
  selectedResourceCount,
  resourceCategoryLabels,
  resourceAriaLabel,
  resourceLimitReason,
  onResourceSelect,
}: {
  readonly children: ReactNode
  readonly commands: readonly ChatCommandDefinition[]
  readonly commandAriaLabel: string
  readonly resourceCandidates: readonly ResourceContextCandidate[]
  readonly selectedResourceCount: number
  readonly resourceCategoryLabels: Readonly<Record<ResourceContextKind, string>>
  readonly resourceAriaLabel: string
  readonly resourceLimitReason: string
  readonly onResourceSelect: (reference: ResourceContextReference) => void
}) {
  return (
    <ComposerPrimitive.Unstable_TriggerPopoverRoot>
      <ChatCommandPopover commands={commands} ariaLabel={commandAriaLabel} />
      <ResourceContextPopover
        candidates={resourceCandidates}
        selectedCount={selectedResourceCount}
        categoryLabels={resourceCategoryLabels}
        ariaLabel={resourceAriaLabel}
        limitReason={resourceLimitReason}
        onSelect={onResourceSelect}
      />
      {children}
    </ComposerPrimitive.Unstable_TriggerPopoverRoot>
  )
}
