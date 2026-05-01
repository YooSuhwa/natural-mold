'use client'

import { ClockIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface CountdownBadgeProps {
  formatted: string
  isUrgent: boolean
  expired: boolean
  label: string
  expiredLabel: string
  className?: string
}

export function CountdownBadge({
  formatted,
  isUrgent,
  expired,
  label,
  expiredLabel,
  className,
}: CountdownBadgeProps) {
  return (
    <div
      className={cn(
        'flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium tabular-nums',
        expired
          ? 'bg-status-warn/15 text-status-warn'
          : isUrgent
            ? 'animate-pulse bg-status-warn/15 text-status-warn'
            : 'bg-muted text-muted-foreground',
        className,
      )}
      aria-live="polite"
      aria-label={`${label}: ${expired ? expiredLabel : formatted}`}
    >
      <ClockIcon className="size-3" />
      <span>{expired ? expiredLabel : formatted}</span>
    </div>
  )
}
