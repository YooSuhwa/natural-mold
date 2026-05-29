'use client'

import { Badge } from '@/components/ui/badge'
import type { ExecutionProfile, SupportLevel } from '@/lib/types/marketplace'
import { cn } from '@/lib/utils'

interface Spec {
  label: string
  className: string
}

const SPECS: Record<SupportLevel, Spec> = {
  ready_python: {
    label: 'Python 실행 가능',
    className: 'bg-status-success/10 text-status-success',
  },
  proxy_http: {
    label: '프록시 필요',
    className: 'bg-status-info/10 text-status-info',
  },
  node_package: {
    label: 'Node 필요',
    className: 'bg-muted text-foreground',
  },
  browser_or_local: {
    label: '브라우저/로컬 필요',
    className: 'bg-status-warn/10 text-status-warn',
  },
  manual_only: {
    label: '수동 설정',
    className: 'bg-status-warn/10 text-status-warn',
  },
  disabled: {
    label: '지원 안 함',
    className: 'bg-destructive/10 text-destructive',
  },
}

interface SupportBadgeProps {
  profile?: ExecutionProfile | null
  className?: string
}

export function SupportBadge({ profile, className }: SupportBadgeProps) {
  const level = profile?.support_level
  if (!level) return null
  const spec = SPECS[level] ?? SPECS.manual_only
  return (
    <Badge className={cn('gap-1', spec.className, className)}>
      <span>{spec.label}</span>
    </Badge>
  )
}
