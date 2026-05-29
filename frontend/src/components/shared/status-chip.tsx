'use client'

import { CheckCircle2, AlertTriangle, Clock, Slash, XCircle, HelpCircle } from 'lucide-react'
import type { ComponentType, SVGProps } from 'react'
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

const STYLES: Record<StatusChipVariant, { icon: Icon; classes: string; defaultLabel: string }> = {
  active: {
    icon: CheckCircle2,
    classes:
      'bg-emerald-100 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30',
    defaultLabel: '활성',
  },
  connected: {
    icon: CheckCircle2,
    classes:
      'bg-emerald-100 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30',
    defaultLabel: '연결됨',
  },
  auth_needed: {
    icon: AlertTriangle,
    classes:
      'bg-amber-100 text-amber-700 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30',
    defaultLabel: '인증 필요',
  },
  expired: {
    icon: Clock,
    classes:
      'bg-amber-100 text-amber-700 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30',
    defaultLabel: '만료됨',
  },
  disabled: {
    icon: Slash,
    classes: 'bg-muted text-muted-foreground ring-border',
    defaultLabel: '비활성',
  },
  error: {
    icon: XCircle,
    classes:
      'bg-rose-100 text-rose-700 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30',
    defaultLabel: '오류',
  },
  unreachable: {
    icon: XCircle,
    classes:
      'bg-rose-100 text-rose-700 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30',
    defaultLabel: '연결 불가',
  },
  unknown: {
    icon: HelpCircle,
    classes: 'bg-muted text-muted-foreground ring-border',
    defaultLabel: '알 수 없음',
  },
  healthy: {
    icon: CheckCircle2,
    classes:
      'bg-emerald-100 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30',
    defaultLabel: '정상',
  },
  degraded: {
    icon: AlertTriangle,
    classes:
      'bg-amber-100 text-amber-700 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30',
    defaultLabel: '주의',
  },
  unhealthy: {
    icon: XCircle,
    classes:
      'bg-rose-100 text-rose-700 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30',
    defaultLabel: '비정상',
  },
}

function resolveVariant(value: string): StatusChipVariant {
  if (value in STYLES) return value as StatusChipVariant
  return 'unknown'
}

export function StatusChip({ variant, label, className }: StatusChipProps) {
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
      {label ?? style.defaultLabel}
    </span>
  )
}
