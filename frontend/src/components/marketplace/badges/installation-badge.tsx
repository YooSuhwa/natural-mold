'use client'

import { memo } from 'react'
import type { LucideIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
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
  labelKey: string
  icon: LucideIcon
  className: string
}

const SPECS: Record<InstallationStatus, Spec> = {
  active: {
    labelKey: 'active',
    icon: CheckCircle2Icon,
    className: 'bg-status-success/10 text-status-success',
  },
  needs_setup: {
    labelKey: 'needsSetup',
    icon: TriangleAlertIcon,
    className: 'bg-status-warn/10 text-status-warn',
  },
  disabled: {
    labelKey: 'disabled',
    icon: BanIcon,
    className: 'bg-destructive/10 text-destructive',
  },
  uninstalled: {
    labelKey: 'uninstalled',
    icon: BanIcon,
    className: 'bg-muted text-muted-foreground',
  },
}

interface InstallationBadgeProps {
  summary?: InstallationSummary | null
  className?: string
}

function InstallationBadgeInner({ summary, className }: InstallationBadgeProps) {
  const t = useTranslations('marketplace.installation')
  if (!summary?.installed || !summary.status) return null
  const spec = SPECS[summary.status] ?? SPECS.active
  const Icon = spec.icon
  return (
    <div className={cn('flex flex-wrap items-center gap-1', className)}>
      <Badge className={cn('gap-1', spec.className)}>
        <Icon className="size-3" aria-hidden />
        <span>{t(spec.labelKey)}</span>
      </Badge>
      {summary.update_available ? (
        <Badge className="gap-1 bg-status-info/10 text-status-info">
          <ArrowUpCircleIcon className="size-3" aria-hidden />
          <span>{t('updateAvailable')}</span>
        </Badge>
      ) : null}
      {summary.dirty ? (
        <Badge className="gap-1 bg-status-accent/10 text-status-accent">
          <PencilIcon className="size-3" aria-hidden />
          <span>{t('dirty')}</span>
        </Badge>
      ) : null}
    </div>
  )
}

export const InstallationBadge = memo(InstallationBadgeInner)
InstallationBadge.displayName = 'InstallationBadge'
