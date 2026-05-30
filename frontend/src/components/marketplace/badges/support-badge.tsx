'use client'

import { memo } from 'react'
import { useTranslations } from 'next-intl'
import { Badge } from '@/components/ui/badge'
import type { ExecutionProfile, SupportLevel } from '@/lib/types/marketplace'
import { cn } from '@/lib/utils'

interface Spec {
  className: string
}

const SPECS: Record<SupportLevel, Spec> = {
  ready_python: {
    className: 'bg-status-success/10 text-status-success',
  },
  proxy_http: {
    className: 'bg-status-info/10 text-status-info',
  },
  node_package: {
    className: 'bg-muted text-foreground',
  },
  browser_or_local: {
    className: 'bg-status-warn/10 text-status-warn',
  },
  manual_only: {
    className: 'bg-status-warn/10 text-status-warn',
  },
  disabled: {
    className: 'bg-destructive/10 text-destructive',
  },
}

interface SupportBadgeProps {
  profile?: ExecutionProfile | null
  className?: string
}

function SupportBadgeInner({ profile, className }: SupportBadgeProps) {
  const t = useTranslations('marketplace.filters.support')
  const level = profile?.support_level
  if (!level) return null
  const spec = SPECS[level] ?? SPECS.manual_only
  const labelKey = {
    ready_python: 'readyPython',
    proxy_http: 'proxyHttp',
    node_package: 'nodePackage',
    browser_or_local: 'browserOrLocal',
    manual_only: 'manualOnly',
    disabled: 'disabled',
  }[level]
  return (
    <Badge className={cn('gap-1', spec.className, className)}>
      <span>{t(labelKey)}</span>
    </Badge>
  )
}

export const SupportBadge = memo(SupportBadgeInner)
SupportBadge.displayName = 'SupportBadge'
