'use client'

import type { LucideIcon } from 'lucide-react'
import {
  CircleDashedIcon,
  CloudIcon,
  KeyIcon,
  LogInIcon,
  PlusIcon,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import type {
  CredentialSummary,
  CredentialSummaryStatus,
} from '@/lib/types/marketplace'
import { cn } from '@/lib/utils'

interface Spec {
  label: string
  icon: LucideIcon
  className: string
}

const SPECS: Record<CredentialSummaryStatus, Spec> = {
  none: {
    label: 'No credential',
    icon: CircleDashedIcon,
    className: 'bg-muted text-muted-foreground',
  },
  optional: {
    label: 'Optional credential',
    icon: PlusIcon,
    className: 'bg-muted text-foreground',
  },
  required: {
    label: 'Credential required',
    icon: KeyIcon,
    className: 'bg-status-warn/10 text-status-warn',
  },
  hosted_proxy: {
    label: 'Hosted proxy',
    icon: CloudIcon,
    className: 'bg-status-info/10 text-status-info',
  },
  manual_login: {
    label: 'Manual login',
    icon: LogInIcon,
    className: 'bg-status-accent/10 text-status-accent',
  },
}

interface CredentialBadgeProps {
  summary?: CredentialSummary | null
  className?: string
}

export function CredentialBadge({ summary, className }: CredentialBadgeProps) {
  if (!summary) return null
  const spec = SPECS[summary.status] ?? SPECS.none
  const Icon = spec.icon
  const missing = summary.missing_required_count > 0
  return (
    <Badge
      className={cn(
        'gap-1',
        spec.className,
        missing && 'ring-1 ring-destructive/40',
        className,
      )}
    >
      <Icon className="size-3" aria-hidden />
      <span>{spec.label}</span>
      {missing ? (
        <span className="ml-1 inline-block size-1.5 rounded-full bg-destructive" aria-hidden />
      ) : null}
    </Badge>
  )
}
