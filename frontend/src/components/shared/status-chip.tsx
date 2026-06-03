'use client'

import { CheckCircle2, AlertTriangle, Clock, Slash, XCircle, HelpCircle } from 'lucide-react'
import type { ComponentType, SVGProps } from 'react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'

export type StatusChipVariant =
  | 'active'
  | 'connected'
  | 'auth_needed'
  | 'expired'
  | 'disabled'
  | 'error'
  | 'unreachable'
  | 'unknown'
  | 'healthy'
  | 'degraded'
  | 'unhealthy'

interface StatusChipProps {
  variant: StatusChipVariant | string
  label?: string
  className?: string
}

type Icon = ComponentType<SVGProps<SVGSVGElement>>

const STYLES: Record<StatusChipVariant, { icon: Icon; classes: string }> = {
  active: {
    icon: CheckCircle2,
    classes: 'moldy-status-surface moldy-status-success',
  },
  connected: {
    icon: CheckCircle2,
    classes: 'moldy-status-surface moldy-status-success',
  },
  auth_needed: {
    icon: AlertTriangle,
    classes: 'moldy-status-surface moldy-status-warn',
  },
  expired: {
    icon: Clock,
    classes: 'moldy-status-surface moldy-status-warn',
  },
  disabled: {
    icon: Slash,
    classes: 'bg-muted text-muted-foreground ring-border',
  },
  error: {
    icon: XCircle,
    classes: 'moldy-status-surface moldy-status-danger',
  },
  unreachable: {
    icon: XCircle,
    classes: 'moldy-status-surface moldy-status-danger',
  },
  unknown: {
    icon: HelpCircle,
    classes: 'bg-muted text-muted-foreground ring-border',
  },
  healthy: {
    icon: CheckCircle2,
    classes: 'moldy-status-surface moldy-status-success',
  },
  degraded: {
    icon: AlertTriangle,
    classes: 'moldy-status-surface moldy-status-warn',
  },
  unhealthy: {
    icon: XCircle,
    classes: 'moldy-status-surface moldy-status-danger',
  },
}

function resolveVariant(value: string): StatusChipVariant {
  if (value in STYLES) return value as StatusChipVariant
  return 'unknown'
}

export function StatusChip({ variant, label, className }: StatusChipProps) {
  const t = useTranslations('common.status')
  const v = resolveVariant(variant)
  const style = STYLES[v]
  const Icon = style.icon
  return (
    <span
      data-status={v}
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        style.classes,
        className,
      )}
    >
      <Icon className="size-3" aria-hidden />
      {label ?? t(v)}
    </span>
  )
}
