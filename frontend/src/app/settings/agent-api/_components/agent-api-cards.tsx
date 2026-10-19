'use client'

import { CheckCircle2Icon, CopyIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function MetricCard({
  label,
  value,
  helper,
  monospace = false,
}: {
  label: string
  value: string
  helper?: string
  monospace?: boolean
}) {
  return (
    <div className="moldy-card space-y-1 p-4">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p
        className={
          monospace ? 'truncate font-mono text-sm text-foreground' : 'text-xl font-semibold'
        }
      >
        {value}
      </p>
      {helper && <p className="text-xs text-muted-foreground">{helper}</p>}
    </div>
  )
}

export function EndpointRow({
  label,
  value,
  copyLabel,
  copyAriaLabel,
  onCopy,
}: {
  label: string
  value: string
  copyLabel: string
  copyAriaLabel: string
  onCopy: (value: string, label: string) => Promise<void>
}) {
  return (
    <div className="moldy-muted-panel flex items-center gap-3 p-3">
      <CheckCircle2Icon className="size-4 text-status-success" />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <p className="truncate font-mono text-xs text-foreground">{value}</p>
      </div>
      <Button
        variant="outline"
        size="icon-sm"
        onClick={() => onCopy(value, copyLabel)}
        aria-label={copyAriaLabel}
      >
        <CopyIcon className="size-4" />
      </Button>
    </div>
  )
}
