import { XIcon } from 'lucide-react'

import type {
  ResourceContextKind,
  ResourceContextReference,
} from '@/lib/chat/context/resource-context'

export function ResourceContextChips({
  references,
  labels,
  removeLabel,
  onRemove,
}: {
  readonly references: readonly ResourceContextReference[]
  readonly labels: Readonly<Record<ResourceContextKind, string>>
  readonly removeLabel: (label: string) => string
  readonly onRemove: (reference: ResourceContextReference) => void
}) {
  if (references.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5 border-b border-border/60 px-3 py-2">
      {references.map((reference) => {
        const label = reference.label ?? reference.id
        const versionKey = reference.kind === 'artifact' ? (reference.version_id ?? '') : ''
        return (
          <span
            key={`${reference.kind}:${reference.id}:${versionKey}`}
            className="moldy-muted-panel inline-flex min-w-0 items-center gap-1.5 px-2 py-1 text-xs"
            data-resource-context-kind={reference.kind}
          >
            <span className="shrink-0 text-muted-foreground">{labels[reference.kind]}</span>
            <span className="max-w-40 truncate font-medium text-foreground">{label}</span>
            <button
              type="button"
              className="text-muted-foreground transition-colors hover:text-foreground"
              aria-label={removeLabel(label)}
              onClick={() => onRemove(reference)}
            >
              <XIcon className="size-3.5" aria-hidden />
            </button>
          </span>
        )
      })}
    </div>
  )
}
