'use client'

import { AlertCircleIcon, InfoIcon } from 'lucide-react'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface Props {
  variant?: 'destructive' | 'info'
  children: ReactNode
  className?: string
}

/**
 * Lightweight Alert — local replacement until shadcn `Alert` is added.
 * Mirrors ADR-010 token usage; no raw palette colors.
 */
export function AuthAlert({ variant = 'destructive', children, className }: Props) {
  const isDestructive = variant === 'destructive'
  const Icon = isDestructive ? AlertCircleIcon : InfoIcon
  return (
    <div
      role="alert"
      aria-live={isDestructive ? 'assertive' : 'polite'}
      className={cn(
        'flex items-start gap-2 rounded-lg border px-3 py-2 text-sm',
        isDestructive
          ? 'border-destructive/30 bg-destructive/10 text-destructive'
          : 'border-border bg-status-info/10 text-status-info',
        className,
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}
