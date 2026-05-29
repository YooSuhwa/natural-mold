'use client'

import { memo } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  ArrowUpCircleIcon,
  BanIcon,
  CheckCircle2Icon,
  PencilIcon,
  TriangleAlertIcon,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import type {
  InstallationStatus,
  InstallationSummary,
} from '@/lib/types/marketplace'
import { cn } from '@/lib/utils'

interface Spec {
  label: string
  icon: LucideIcon
  className: string
}

const SPECS: Record<InstallationStatus, Spec> = {
  active: {
    label: '설치됨',
    icon: CheckCircle2Icon,
    className: 'bg-status-success/10 text-status-success',
  },
  needs_setup: {
    label: '설정 필요',
    icon: TriangleAlertIcon,
    className: 'bg-status-warn/10 text-status-warn',
  },
  disabled: {
    label: '비활성화',
    icon: BanIcon,
    className: 'bg-destructive/10 text-destructive',
  },
  uninstalled: {
    label: '미설치',
    icon: BanIcon,
    className: 'bg-muted text-muted-foreground',
  },
}

interface InstallationBadgeProps {
  summary?: InstallationSummary | null
  className?: string
}

function InstallationBadgeInner({ summary, className }: InstallationBadgeProps) {
  if (!summary?.installed || !summary.status) return null
  const spec = SPECS[summary.status] ?? SPECS.active
  const Icon = spec.icon
  return (
    <div className={cn('flex flex-wrap items-center gap-1', className)}>
      <Badge className={cn('gap-1', spec.className)}>
        <Icon className="size-3" aria-hidden />
        <span>{spec.label}</span>
      </Badge>
      {summary.update_available ? (
        <Badge className="gap-1 bg-status-info/10 text-status-info">
          <ArrowUpCircleIcon className="size-3" aria-hidden />
          <span>업데이트 가능</span>
        </Badge>
      ) : null}
      {summary.dirty ? (
        <Badge className="gap-1 bg-status-accent/10 text-status-accent">
          <PencilIcon className="size-3" aria-hidden />
          <span>수정됨</span>
        </Badge>
      ) : null}
    </div>
  )
}

export const InstallationBadge = memo(InstallationBadgeInner)
InstallationBadge.displayName = 'InstallationBadge'
