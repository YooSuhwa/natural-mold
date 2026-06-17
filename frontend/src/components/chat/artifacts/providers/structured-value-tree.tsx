import type { StructuredPreviewValue } from '../data-preview-utils'

function isObjectValue(
  value: StructuredPreviewValue,
): value is { [key: string]: StructuredPreviewValue } {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function formatScalarValue(value: StructuredPreviewValue): string {
  if (value === null) return 'null'
  if (typeof value === 'string') return JSON.stringify(value)
  return String(value)
}

function valueTone(value: StructuredPreviewValue): string {
  if (typeof value === 'string') return 'moldy-data-type-string'
  if (typeof value === 'number') return 'moldy-data-type-number'
  if (typeof value === 'boolean') return 'moldy-data-type-boolean'
  if (value === null) return 'text-muted-foreground'
  return 'text-foreground'
}

function branchSummary(value: StructuredPreviewValue): string {
  if (Array.isArray(value)) return `[${value.length}]`
  if (isObjectValue(value)) return `{${Object.keys(value).length}}`
  return ''
}

interface StructuredValueNodeProps {
  name?: string
  value: StructuredPreviewValue
}

function StructuredValueNode({ name, value }: StructuredValueNodeProps) {
  if (Array.isArray(value) || isObjectValue(value)) {
    const entries = Array.isArray(value)
      ? value.map((nested, index) => [String(index), nested] as const)
      : Object.entries(value)

    return (
      <details open className="group">
        <summary className="flex cursor-pointer items-center gap-2 border-b border-border/40 py-1 text-xs">
          {name ? <span className="font-medium text-foreground">{name}</span> : null}
          <span className="font-mono text-muted-foreground">{branchSummary(value)}</span>
        </summary>
        <div className="ml-3 border-l border-border/60 pl-3">
          {entries.length ? (
            entries.map(([key, nested]) => (
              <StructuredValueNode key={key} name={key} value={nested} />
            ))
          ) : (
            <div className="py-1 text-xs text-muted-foreground">{branchSummary(value)}</div>
          )}
        </div>
      </details>
    )
  }

  return (
    <div className="flex gap-3 border-b border-border/40 py-1 text-xs">
      {name ? (
        <span className="w-24 shrink-0 truncate font-medium text-foreground">{name}</span>
      ) : (
        <span className="w-24 shrink-0" />
      )}
      <span className={`break-words font-mono ${valueTone(value)}`}>
        {formatScalarValue(value)}
      </span>
    </div>
  )
}

interface StructuredTreePreviewProps {
  value: StructuredPreviewValue
}

export function StructuredValueTree({ value }: StructuredTreePreviewProps) {
  return (
    <div className="max-h-[520px] overflow-auto border border-border bg-card px-3 py-2">
      <StructuredValueNode value={value} />
    </div>
  )
}
